"""测试 polaris.kernelone.shared 模块。

验证拆分后的共享工具模块的正确性。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from polaris.kernelone.shared import (
    # terminal
    ANSI_COLORS,
    ANSI_RESET,
    FILE_BLOCK_RE,
    FILE_BLOCK_REGEX,
    RATE_LIMIT_SECONDS_RE,
    append_log,
    colorize,
    compact_str,
    extract_rate_limit_seconds,
    extract_text_from_content,
    is_docs_path,
    is_ignorable_error_line,
    is_safe_path,
    normalize_bool,
    normalize_int,
    # path_utils
    normalize_path,
    normalize_path_list,
    normalize_path_safe,
    normalize_policy_decision,
    normalize_positive_int,
    normalize_str_list,
    normalize_timeout_seconds,
    safe_float,
    safe_int,
    # text_utils
    safe_truncate,
    strip_ansi,
    supports_color,
    timeout_seconds_or_none,
    truncate_text,
    unique_preserve,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestTerminal:
    """测试终端颜色模块。"""

    def test_ansi_colors_defined(self) -> None:
        """验证 ANSI 颜色常量已定义。"""
        assert isinstance(ANSI_COLORS, dict)
        assert "ERROR" in ANSI_COLORS
        assert "INFO" in ANSI_COLORS
        assert ANSI_RESET == "\x1b[0m"

    def test_set_ansi_enabled(self) -> None:
        """验证 ANSI 启用状态设置。

        注意: 由于模块级全局变量在测试间可能共享状态，
        我们只验证函数调用不会抛出异常且状态被设置。
        """
        from polaris.kernelone.shared import terminal

        # 保存原始状态
        original = terminal.ANSI_ENABLED
        try:
            # 测试启用
            terminal.set_ansi_enabled(True)
            assert terminal.ANSI_ENABLED is True
            # 测试禁用
            terminal.set_ansi_enabled(False)
            assert terminal.ANSI_ENABLED is False
        finally:
            # 恢复原始状态
            terminal.set_ansi_enabled(original)

    def test_supports_color(self) -> None:
        """验证 supports_color 函数。"""
        # 默认应该是 False（因为不是 tty）
        result = supports_color()
        assert isinstance(result, bool)

    def test_colorize_with_disabled(self) -> None:
        """验证禁用颜色时的 colorize 输出。"""
        result = colorize("ERROR", "message", enabled=False)
        assert "[ERROR] message" in result
        assert ANSI_RESET not in result

    def test_colorize_with_enabled(self) -> None:
        """验证启用颜色时的 colorize 输出。"""
        result = colorize("ERROR", "message", enabled=True)
        assert "[ERROR] message" in result
        assert ANSI_RESET in result


class TestTextUtils:
    """测试文本工具模块。"""

    def test_safe_truncate_short_text(self) -> None:
        """验证短文本不被截断。"""
        text = "Hello"
        result = safe_truncate(text, limit=10)
        assert result == "Hello"

    def test_safe_truncate_long_text(self) -> None:
        """验证长文本被截断。"""
        text = "Hello World"
        result = safe_truncate(text, limit=5)
        assert result == "Hello..."

    def test_strip_ansi(self) -> None:
        """验证移除 ANSI 转义序列。"""
        text = "\x1b[31mRed Text\x1b[0m"
        result = strip_ansi(text)
        assert result == "Red Text"

    def test_strip_ansi_empty(self) -> None:
        """验证空字符串处理。"""
        assert strip_ansi("") == ""
        # strip_ansi expects str, not None; test empty string behavior

    def test_safe_int_integer(self) -> None:
        """验证整数输入。"""
        assert safe_int(42) == 42

    def test_safe_int_string(self) -> None:
        """验证字符串输入。"""
        assert safe_int("42") == 42
        assert safe_int("  42  ") == 42

    def test_safe_int_default(self) -> None:
        """验证默认值。"""
        assert safe_int("invalid") == -1
        assert safe_int("invalid", default=99) == 99
        assert safe_int(None) == -1
        assert safe_int(None, default=0) == 0

    def test_safe_float(self) -> None:
        """验证浮点数转换。"""
        assert safe_float(3.14) == 3.14
        assert safe_float("3.14") == 3.14
        assert safe_float("invalid", default=1.0) == 1.0

    def test_file_block_re_matches(self) -> None:
        """验证 FILE_BLOCK_RE 正则匹配。"""
        content = '<file path="hello.py">\nprint("ok")\n</file>'
        match = FILE_BLOCK_RE.search(content)
        assert match is not None
        assert match.group(1) == "hello.py"
        assert 'print("ok")' in match.group(2)

    def test_file_block_regex_alias(self) -> None:
        """验证 FILE_BLOCK_REGEX 别名。"""
        assert FILE_BLOCK_REGEX is FILE_BLOCK_RE

    def test_rate_limit_seconds_re(self) -> None:
        """验证速率限制正则。

        注意: 正则期望 digits 紧跟在 : 后（无空格）。
        """
        # 使用正确的 JSON 格式（无反斜杠）
        text = '{"resets_in_seconds":60}'
        match = RATE_LIMIT_SECONDS_RE.search(text)
        assert match is not None
        assert match.group(1) == "60"

    def test_extract_rate_limit_seconds_seconds(self) -> None:
        """验证提取秒数格式的速率限制。

        注意: 正则期望 digits 紧跟在 : 后（无空格）。
        """
        # 使用正确的 JSON 格式（无反斜杠）
        text = '{"resets_in_seconds":120}'
        assert extract_rate_limit_seconds(text) == 120

    def test_is_ignorable_error_line(self) -> None:
        """验证可忽略的错误行。"""
        assert is_ignorable_error_line("rmcp::transport::worker error") is True
        assert is_ignorable_error_line("invalid_token received") is True
        assert is_ignorable_error_line("Normal log message") is False

    def test_unique_preserve(self) -> None:
        """验证去重保持顺序。"""
        items = ["a", "b", "a", "c", "b"]
        result = unique_preserve(items)
        assert result == ["a", "b", "c"]

    def test_extract_text_from_content_string(self) -> None:
        """验证从字符串提取文本。"""
        assert extract_text_from_content("  hello  ") == "hello"

    def test_extract_text_from_content_dict(self) -> None:
        """验证从字典提取文本。"""
        assert extract_text_from_content({"text": "hello"}) == "hello"
        assert extract_text_from_content({"content": "world"}) == "world"

    def test_extract_text_from_content_list(self) -> None:
        """验证从列表提取文本。"""
        content = [
            {"type": "text", "text": "hello"},
            {"type": "output_text", "text": "world"},
        ]
        result = extract_text_from_content(content)
        assert "hello" in result
        assert "world" in result

    def test_truncate_text(self) -> None:
        """验证文本截断。"""
        text = "x" * 1000
        result = truncate_text(text, limit=100)
        assert len(result) == 103  # 100 + "..."
        assert result.endswith("...")

    def test_truncate_text_unicode(self) -> None:
        """验证 Unicode 文本截断（B-14 修复）。

        确保截断不会在多字节字符中间进行。
        """
        # 中文字符（每个占 3 字节 UTF-8）
        chinese_text = "中" * 100
        result = truncate_text(chinese_text, limit=5)
        # 结果必须是有效的 UTF-8
        result.encode("utf-8")
        assert result.endswith("...")

        # 日文字符（每个占 3 字节 UTF-8）
        japanese_text = "日" * 50 + "本" * 50
        result = truncate_text(japanese_text, limit=10)
        result.encode("utf-8")
        assert result.endswith("...")

        # Emoji（每个占 4 字节 UTF-8）
        emoji_text = "🎉" * 50
        result = truncate_text(emoji_text, limit=3)
        result.encode("utf-8")
        assert result.endswith("...")

    def test_normalize_str_list(self) -> None:
        """验证字符串列表规范化。"""
        assert normalize_str_list(None) == []
        assert normalize_str_list([]) == []
        assert normalize_str_list(["a", "b"]) == ["a", "b"]
        assert normalize_str_list("single") == ["single"]
        assert normalize_str_list([{"path": "/test"}]) == ["/test"]

    def test_normalize_bool(self) -> None:
        """验证布尔值规范化。"""
        assert normalize_bool(True) is True
        assert normalize_bool(False) is False
        assert normalize_bool("true") is True
        assert normalize_bool("1") is True
        assert normalize_bool("yes") is True
        assert normalize_bool("false") is False
        assert normalize_bool("0") is False
        assert normalize_bool("invalid", default=True) is True
        assert normalize_bool(None, default=False) is False

    def test_normalize_int(self) -> None:
        """验证整数规范化。"""
        assert normalize_int(42) == 42
        assert normalize_int("42") == 42
        assert normalize_int("  42  ") == 42
        assert normalize_int("invalid") == 0
        assert normalize_int("invalid", default=99) == 99

    def test_normalize_positive_int(self) -> None:
        """验证正整数规范化。"""
        assert normalize_positive_int(0) == 1
        assert normalize_positive_int(-5) == 1
        assert normalize_positive_int(10) == 10

    def test_normalize_timeout_seconds(self) -> None:
        """验证超时秒数规范化。"""
        assert normalize_timeout_seconds(30) == 30
        assert normalize_timeout_seconds(0) == 0
        assert normalize_timeout_seconds(-5) == 0

    def test_timeout_seconds_or_none(self) -> None:
        """验证超时秒数或 None。"""
        assert timeout_seconds_or_none(30) == 30
        assert timeout_seconds_or_none(0) is None
        assert timeout_seconds_or_none(-5) is None

    def test_append_log(self, tmp_path: Path) -> None:
        """验证日志追加。"""
        log_file = tmp_path / "test.log"
        append_log(str(log_file), "Line 1\n")
        append_log(str(log_file), "Line 2\n")
        content = log_file.read_text(encoding="utf-8")
        assert "Line 1" in content
        assert "Line 2" in content

    def test_compact_str(self) -> None:
        """验证字符串压缩。"""
        assert compact_str(None, 10) == ""
        assert compact_str(42, 10) == "42"
        assert compact_str("  hello world  ", 5) == "hello..."

    def test_normalize_policy_decision(self) -> None:
        """验证策略决策规范化。"""
        assert normalize_policy_decision("allow") == "allow"
        assert normalize_policy_decision("pass") == "allow"
        assert normalize_policy_decision("approved") == "allow"
        assert normalize_policy_decision("block") == "block"
        assert normalize_policy_decision("deny") == "block"
        assert normalize_policy_decision("escalate") == "escalate"
        assert normalize_policy_decision("invalid") == ""


class TestPathUtils:
    """测试路径工具模块。"""

    def test_normalize_path_simple(self) -> None:
        """验证简单路径规范化。"""
        assert normalize_path("path/to/file") == "path/to/file"

    def test_normalize_path_with_dots(self) -> None:
        """验证带点的路径规范化。"""
        assert normalize_path("path/to/../file") == "path/file"
        assert normalize_path("path/./file") == "path/file"

    def test_normalize_path_quotes(self) -> None:
        """验证去除引号。"""
        assert normalize_path("'path/to/file'") == "path/to/file"
        assert normalize_path('"path/to/file"') == "path/to/file"

    def test_normalize_path_trailing_punctuation(self) -> None:
        """验证去除尾部标点。"""
        assert normalize_path("path/to/file,") == "path/to/file"
        assert normalize_path("path/to/file.") == "path/to/file"

    def test_normalize_path_windows_separator(self) -> None:
        """验证 Windows 分隔符转换。"""
        assert normalize_path("path\\to\\file") == "path/to/file"

    def test_normalize_path_leading_slash(self) -> None:
        """验证绝对路径保留前导斜杠（Unix 绝对路径不应被破坏）。"""
        # BUG 修复：Unix 绝对路径应该保留 / 前缀
        assert normalize_path("/path/to/file") == "/path/to/file"
        assert normalize_path("/absolute/path") == "/absolute/path"

    def test_normalize_path_dangerous(self) -> None:
        """验证危险路径拒绝。"""
        assert normalize_path("../etc/passwd") == ""
        assert normalize_path("path/../../etc/passwd") == ""
        assert normalize_path("") == ""

    def test_normalize_path_list(self) -> None:
        """验证路径列表规范化。

        注意: normalize_path 现在保留绝对路径的 / 前缀
        """
        assert normalize_path_list(None) == []
        assert normalize_path_list([]) == []
        assert normalize_path_list(["a", "b"]) == ["a", "b"]
        assert normalize_path_list("single") == ["single"]
        # 绝对路径现在会保留 / 前缀
        assert normalize_path_list([{"path": "/test"}]) == ["/test"]
        assert normalize_path_list([{"path": "test"}]) == ["test"]
        assert normalize_path_list([{"path": "path/to/file"}]) == ["path/to/file"]

    def test_normalize_path_safe(self) -> None:
        """验证安全路径规范化。"""
        result = normalize_path_safe("path/to/file")
        assert result is not None
        # Path 对象会根据操作系统使用分隔符
        assert "path" in str(result)

        result = normalize_path_safe("")
        assert result is None

        result = normalize_path_safe("path")
        assert result is not None

    def test_is_docs_path(self) -> None:
        """验证 docs 路径检查。"""
        assert is_docs_path("docs") is True
        assert is_docs_path("docs/") is True
        assert is_docs_path("docs/readme.md") is True
        assert is_docs_path("DOCS/readme.md") is True
        assert is_docs_path("src/file.py") is False
        assert is_docs_path("") is False

    def test_is_safe_path(self) -> None:
        """验证路径安全检查。"""
        assert is_safe_path("path/to/file") is True
        assert is_safe_path("path/../file") is False
        assert is_safe_path("path\x00file") is False


class TestBackwardCompatibility:
    """测试向后兼容性。"""

    def test_shared_types_import(self) -> None:
        """验证从 runtime.shared_types 导入仍然有效。"""
        from polaris.kernelone.runtime.shared_types import (
            ANSI_COLORS as _AC,
            ANSI_RESET as _AR,
            FILE_BLOCK_RE as _FBR,
            normalize_path as _np,
            safe_truncate as _st,
        )

        assert _AC == ANSI_COLORS
        assert _AR == ANSI_RESET
        assert _st == safe_truncate
        assert _np == normalize_path
        assert _FBR is FILE_BLOCK_RE
