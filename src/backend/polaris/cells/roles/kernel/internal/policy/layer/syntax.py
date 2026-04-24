"""L3 Runtime Syntax Policy - 代码生成语法硬性约束层.

此模块在 LLM 输出提交前进行语法预检，拦截高频错误模式（如 return0, if( 等），
防止因 LLM 生成代码时的常见语病导致工具执行失败。

架构位置: L3 运行时契约层 (Tri-Axis Y轴 Profession 补充)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class SyntaxValidationResult:
    """语法验证结果."""

    valid: bool
    error: str = ""
    line_number: int | None = None
    suggestion: str = ""


class SyntaxPolicy:
    """L3 运行时语法契约策略.

    在代码生成工具调用前拦截常见语法错误模式，强制要求 LLM 重新生成。
    这是防止 precision_edit/precision_edit 因 'return0' 等错误反复失败的最后防线。
    """

    # 高频错误模式: (正则, 错误描述, 修复建议)
    PYTHON_FORBIDDEN_PATTERNS: list[tuple[str, str, str]] = [
        # return0 类错误: 关键字与值之间缺少空格
        (r"\breturn0\b", "'return' 与 '0' 之间缺少空格", "使用 'return 0' (return + 空格 + 0)"),
        (r"\breturnNone\b", "'return' 与 'None' 之间缺少空格", "使用 'return None'"),
        (r"\breturnTrue\b", "'return' 与 'True' 之间缺少空格", "使用 'return True'"),
        (r"\breturnFalse\b", "'return' 与 'False' 之间缺少空格", "使用 'return False'"),
        (r"\bprint0\b", "'print' 与 '0' 之间缺少空格或括号", "使用 'print(0)'"),
        # 控制流关键字后缺少空格
        (r"\bif\(", "'if' 与 '(' 之间缺少空格", "使用 'if (' (if + 空格 + ()"),
        (r"\bfor\(", "'for' 与 '(' 之间缺少空格", "使用 'for (' (for + 空格 + ()"),
        (r"\bwhile\(", "'while' 与 '(' 之间缺少空格", "使用 'while (' (while + 空格 + ()"),
        (r"\belif\(", "'elif' 与 '(' 之间缺少空格", "使用 'elif (' (elif + 空格 + ()"),
        (r"\bdef\(", "'def' 与函数名之间格式错误", "使用 'def funcname(' (def + 空格 + 名称)"),
        # 赋值和比较运算符粘连
        (r"\bif\s*\w+==", "比较运算符 '==' 两侧应有空格", "使用 'if x == y' (变量 + 空格 + == + 空格 + 值)"),
        (r"\bif\s*\w+!=", "比较运算符 '!=' 两侧应有空格", "使用 'if x != y' (变量 + 空格 + != + 空格 + 值)"),
        # 缩进异常 (检测Tab或异常空格)
        (r"^\s+\w+.*:\s*\n\s*\w+", "可能缺少正确缩进", "确保冒号后的代码块有4空格缩进"),
    ]

    JAVASCRIPT_FORBIDDEN_PATTERNS: list[tuple[str, str, str]] = [
        (r"\bif\(", "'if' 与 '(' 之间缺少空格", "使用 'if ('"),
        (r"\bfor\(", "'for' 与 '(' 之间缺少空格", "使用 'for ('"),
        (r"\bwhile\(", "'while' 与 '(' 之间缺少空格", "使用 'while ('"),
        (r"\bfunction\(", "'function' 格式错误", "使用 'function name(' 或箭头函数"),
    ]

    def __init__(self) -> None:
        """初始化语法策略."""
        self._compiled_python = [
            (re.compile(pattern), error, suggestion) for pattern, error, suggestion in self.PYTHON_FORBIDDEN_PATTERNS
        ]
        self._compiled_js = [
            (re.compile(pattern), error, suggestion)
            for pattern, error, suggestion in self.JAVASCRIPT_FORBIDDEN_PATTERNS
        ]

    def validate_code(
        self,
        content: str,
        language: str = "python",
        file_path: str | None = None,
    ) -> SyntaxValidationResult:
        """验证代码内容是否包含禁止的错误模式.

        Args:
            content: 要验证的代码内容
            language: 编程语言 (python/javascript)
            file_path: 可选的文件路径（用于错误报告）

        Returns:
            SyntaxValidationResult: 验证结果
        """
        if not content or not content.strip():
            return SyntaxValidationResult(valid=True)

        patterns = self._compiled_python if language == "python" else self._compiled_js

        lines = content.split("\n")
        for line_idx, line in enumerate(lines, start=1):
            for pattern_regex, error_desc, suggestion in patterns:
                if pattern_regex.search(line):
                    location = f"第 {line_idx} 行" if file_path is None else f"{file_path}:{line_idx}"
                    return SyntaxValidationResult(
                        valid=False,
                        error=f"[SYNTAX POLICY] {location}: {error_desc}",
                        line_number=line_idx,
                        suggestion=f"修复建议: {suggestion}",
                    )

        return SyntaxValidationResult(valid=True)

    def validate_precision_edit_search(
        self,
        search_text: str,
        file_path: str | None = None,
    ) -> SyntaxValidationResult:
        """专门验证 precision_edit 的 search 参数.

        precision_edit 最容易因 'return0' 类错误失败，此函数提供针对性检查。

        Args:
            search_text: precision_edit 的 search 参数
            file_path: 目标文件路径（用于推断语言）

        Returns:
            SyntaxValidationResult: 验证结果
        """
        # 根据文件扩展名推断语言
        language = "python"
        if file_path:
            if file_path.endswith((".js", ".ts", ".jsx", ".tsx")):
                language = "javascript"
            elif file_path.endswith((".py", ".pyi")):
                language = "python"

        result = self.validate_code(search_text, language, file_path)
        if not result.valid:
            # 为 precision_edit 场景增强错误消息
            return SyntaxValidationResult(
                valid=False,
                error=f"[precision_edit 语法拦截] {result.error}",
                line_number=result.line_number,
                suggestion=f"{result.suggestion}\n\n重要提示: search 字符串必须与文件中的代码完全一致（包括空格）。建议使用 read_file() 先读取文件内容，复制其中的精确文本。",
            )
        return result

    def validate_repo_apply_diff(
        self,
        diff_content: str,
    ) -> SyntaxValidationResult:
        """验证 repo_apply_diff 的 diff 内容.

        检查 diff 中的 '-' 行（删除行）和 '+' 行（新增行）是否符合语法规范。

        Args:
            diff_content: unified diff 格式的内容

        Returns:
            SyntaxValidationResult: 验证结果
        """
        if not diff_content:
            return SyntaxValidationResult(valid=True)

        lines = diff_content.split("\n")
        in_hunk = False

        for line_idx, line in enumerate(lines, start=1):
            # 检测 hunk 头
            if line.startswith("@@"):
                in_hunk = True
                continue

            if not in_hunk:
                continue

            # 检查新增行 (+ 开头)
            if line.startswith("+") and not line.startswith("+++"):
                code_line = line[1:]  # 去掉 '+'
                result = self.validate_code(code_line, "python")
                if not result.valid:
                    return SyntaxValidationResult(
                        valid=False,
                        error=f"[repo_apply_diff 语法拦截] 第 {line_idx} 行 (新增代码): {result.error}",
                        line_number=line_idx,
                        suggestion=f"{result.suggestion}\n\n请修正 diff 中的语法错误后再提交。",
                    )

            # 检查删除行 (- 开头) - 主要是确认搜索文本正确
            if line.startswith("-") and not line.startswith("---"):
                code_line = line[1:]  # 去掉 '-'
                # 对删除行只做轻量检查，因为可能是旧代码
                for pattern_regex, _error_desc, _suggestion in self._compiled_python:
                    if pattern_regex.search(code_line):
                        # 仅警告，不阻止（可能是要修复的错误）
                        pass  # 删除行允许有错误，因为就是要替换它

        return SyntaxValidationResult(valid=True)


# 全局单例
_syntax_policy_instance: SyntaxPolicy | None = None


def get_syntax_policy() -> SyntaxPolicy:
    """获取全局语法策略单例."""
    global _syntax_policy_instance
    if _syntax_policy_instance is None:
        _syntax_policy_instance = SyntaxPolicy()
    return _syntax_policy_instance
