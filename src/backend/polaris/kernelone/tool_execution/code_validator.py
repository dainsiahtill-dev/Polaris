"""Code Syntax Validator - LLM 幻觉智能修复层。

Architecture:
    LLM Output → [Smart Fix Layer] → Kernel FS
                        ↓
              ┌─────────────────────────────────┐
              │ 1. Third-party tool format      │
              │    (ruff/prettier/gofmt/rustfmt)│
              │ 2. Regex hallucination fixes     │
              │    (return0 → return 0)          │
              │ 3. AST/bracket validation       │
              └─────────────────────────────────┘
                        ↓
              Auto-fix and continue

设计哲学: 不要求 LLM perfectionist，智能兜底修复小错误。
"""

from __future__ import annotations

import ast
import builtins
import logging
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SyntaxValidationResult:
    """语法验证结果。"""

    is_valid: bool
    errors: list[CodeSyntaxError] | None = None
    suggestions: list[str] | None = None
    fixed_code: str | None = None  # 自动修复后的代码

    @classmethod
    def success(cls, fixed_code: str | None = None) -> SyntaxValidationResult:
        """创建成功的验证结果。

        Args:
            fixed_code: 如果自动修复了幻觉错误，返回修复后的代码
        """
        return cls(is_valid=True, fixed_code=fixed_code)

    @classmethod
    def failure(
        cls,
        errors: list[CodeSyntaxError],
        suggestions: list[str] | None = None,
    ) -> SyntaxValidationResult:
        """创建失败的验证结果。"""
        return cls(is_valid=False, errors=errors, suggestions=suggestions or [])


@dataclass
class CodeSyntaxError:
    """语法错误详情。"""

    line: int
    column: int
    message: str
    error_type: str  # IndentationError, SyntaxError, etc.
    code_snippet: str | None = None


@dataclass
class HallucinationFix:
    """LLM幻觉自动修复详情。"""

    original: str  # 错误文本
    fixed: str  # 修复后文本
    explanation: str  # 解释
    line: int  # 行号
    confidence: float = 0.95  # 修复置信度


class PythonCodeValidator:
    """Python代码语法验证器。"""

    # 常见的LLM生成代码错误模式
    # 只检测明确的语法错误，不匹配正常的代码结构
    HALLUCINATION_PATTERNS = [
        # 关键字后面缺少空格（这些是真正的错误）
        (r"\breturn0\b", "Did you mean 'return 0'?"),
        (r"\breturn1\b", "Did you mean 'return 1'?"),
        (r"\breturnNone\b", "Did you mean 'return None'?"),
        (r"\breturnTrue\b", "Did you mean 'return True'?"),
        (r"\breturnFalse\b", "Did you mean 'return False'?"),
        (r"\bprint0\b", "Did you mean 'print(0)'?"),
        (r"\bprint1\b", "Did you mean 'print(1)'?"),
        # 关键字后面缺少空格
        (r"\bif\(", "Did you mean 'if ('?"),
        (r"\bfor\(", "Did you mean 'for ('?"),
        (r"\bwhile\(", "Did you mean 'while ('?"),
        (r"\belif\(", "Did you mean 'elif ('?"),
        (r"\bdef\(", "Did you mean 'def ('?"),
        # 函数调用缺少括号
        (r"\bprint\s+[^\(]", "print requires parentheses: print(...)"),
    ]

    # 自动修复模式: (错误模式, 修复后文本, 解释)
    FIX_PATTERNS: list[tuple[str, str, str]] = [
        # return0 -> return 0
        (r"\breturn0\b", "return 0", "Missing space between 'return' and value"),
        (r"\breturn1\b", "return 1", "Missing space between 'return' and value"),
        (r"\breturnNone\b", "return None", "Missing space between 'return' and None"),
        (r"\breturnTrue\b", "return True", "Missing space between 'return' and True"),
        (r"\breturnFalse\b", "return False", "Missing space between 'return' and False"),
        # if( -> if (
        (r"\bif\(([^\)]+)\)", r"if (\1)", "Missing space after 'if'"),
        # for( -> for (
        (r"\bfor\(([^\)]+)\)", r"for (\1)", "Missing space after 'for'"),
        # while( -> while (
        (r"\bwhile\(([^\)]+)\)", r"while (\1)", "Missing space after 'while'"),
        # elif( -> elif (
        (r"\belif\(([^\)]+)\)", r"elif (\1)", "Missing space after 'elif'"),
        # def( -> def (
        (r"\bdef\(([^\)]+)\)", r"def (\1)", "Missing space after 'def'"),
        # print x -> print(x) (but not print(...))
        (r"\bprint\s+(?!\()([^(\n]+)", r"print(\1)", "print() requires parentheses"),
    ]

    def validate(self, code: str, filepath: str | None = None) -> SyntaxValidationResult:
        """验证Python代码语法，自动修复可识别的幻觉错误。

        Args:
            code: 待验证的代码内容
            filepath: 文件路径（用于错误报告）

        Returns:
            语法验证结果（包含修复信息）
        """
        if not code or not code.strip():
            return SyntaxValidationResult.failure(
                [CodeSyntaxError(line=0, column=0, message="Empty code", error_type="EmptyCode")]
            )

        # 第一步：尝试自动修复LLM幻觉
        fixed_code, fixes = self.fix(code)

        # 第二步：验证修复后的代码（AST解析）
        try:
            ast.parse(fixed_code)
        except IndentationError as e:
            # IndentationError 是 SyntaxError 的子类，必须先捕获
            return self._handle_indentation_error(e, code, filepath)
        except builtins.SyntaxError as e:
            return self._handle_syntax_error(e, code, filepath)
        except Exception as e:
            # 未知错误，返回成功但不阻止执行
            logger.warning("Unknown validation error: %s", e)
            return SyntaxValidationResult.success()

        # AST parsing succeeded - 检查是否有未修复的幻觉模式
        is_clean, errors = self.quick_check(fixed_code)
        if not is_clean:
            # 仍有未修复的幻觉，返回错误
            syntax_errors: list[CodeSyntaxError] = []
            suggestions: list[str] = []
            for error in errors:
                parts = error.split(":", 2)
                if len(parts) >= 3:
                    try:
                        line_num = int(parts[0].replace("Line ", ""))
                        message = parts[2].strip()
                        syntax_errors.append(
                            CodeSyntaxError(
                                line=line_num,
                                column=0,
                                message=message,
                                error_type="LLMHallucination",
                            )
                        )
                        suggestions.append(message)
                    except ValueError:
                        syntax_errors.append(
                            CodeSyntaxError(
                                line=0,
                                column=0,
                                message=error,
                                error_type="LLMHallucination",
                            )
                        )
                        suggestions.append(error)

            return SyntaxValidationResult.failure(syntax_errors, suggestions)

        # 如果有修复，返回修复后的代码
        if fixes:
            return SyntaxValidationResult.success(fixed_code=fixed_code)

        return SyntaxValidationResult.success()

    def _handle_syntax_error(
        self,
        error: builtins.SyntaxError,
        code: str,
        filepath: str | None,
    ) -> SyntaxValidationResult:
        """处理语法错误。"""
        errors = [
            CodeSyntaxError(
                line=error.lineno or 0,
                column=error.offset or 0,
                message=str(error.msg),
                error_type="SyntaxError",
            )
        ]

        suggestions = []

        # 检查是否是常见的LLM幻觉错误
        line_text = ""
        if error.lineno and 0 < error.lineno <= len(code.splitlines()):
            line_text = code.splitlines()[error.lineno - 1]

        for pattern, suggestion in self.HALLUCINATION_PATTERNS:
            if re.search(pattern, line_text):
                suggestions.append(suggestion)

        # 如果有具体建议，添加到错误消息
        if suggestions and error.msg:
            errors[0].message = f"{error.msg}. {' '.join(suggestions)}"

        return SyntaxValidationResult.failure(errors, suggestions)

    def _handle_indentation_error(
        self,
        error: IndentationError,
        code: str,
        filepath: str | None,
    ) -> SyntaxValidationResult:
        """处理缩进错误。"""
        errors = [
            CodeSyntaxError(
                line=error.lineno or 0,
                column=error.offset or 0,
                message=str(error.msg),
                error_type="IndentationError",
            )
        ]

        suggestions = []

        if not error.lineno or error.lineno > len(code.splitlines()):
            return SyntaxValidationResult.failure(errors, suggestions)

        lines = code.splitlines()
        line_text = lines[error.lineno - 1]
        leading_spaces = len(line_text) - len(line_text.lstrip())

        # 检查是否使用了tabs而不是空格
        if "\t" in line_text:
            suggestions.append("Use spaces instead of tabs for indentation")

        # 检查是否缩进不正确（不是4的倍数）
        if leading_spaces % 4 != 0 and leading_spaces > 0:
            suggestions.append(f"Indentation should be a multiple of 4 (found {leading_spaces})")

        # 检查是否有混合的缩进
        if "    " in line_text and "\t" in line_text:
            suggestions.append("Do not mix tabs and spaces for indentation")

        # 检查是否是函数定义后缺少缩进
        if re.match(r"^\s*(def|class|if|for|while|try|except|finally|with):", line_text):
            suggestions.append("The body of a control structure must be indented with 4 spaces")

        # 上下文感知的结构性建议
        if "unindent does not match" in error.msg or "unexpected indent" in error.msg:
            # 检查是否整个文件缩进都是错的
            all_indents = []
            for i, line in enumerate(lines[: error.lineno]):
                if line.strip():
                    ind = len(line) - len(line.lstrip())
                    all_indents.append(ind)

            if all_indents:
                # 如果缩进都很小（1-2），很可能是整体缩进错误
                if all(i < 4 for i in all_indents if i > 0):
                    suggestions.append(
                        "The entire file seems to use 1-space indentation instead of 4-space. "
                        "Python requires 4 spaces per indentation level."
                    )
                    line2_leading = len(lines[1]) - len(lines[1].lstrip())
                    suggestions.append(f"Line 2 'if not values:' has {line2_leading} space(s) but needs 4 spaces.")
                else:
                    suggestions.append(
                        "This error usually means a previous line has wrong indentation. "
                        "Check that each block (if/for/while/def) uses exactly 4 spaces."
                    )
            else:
                suggestions.append("Indentation error: each nested block needs exactly 4 spaces of indentation.")

        return SyntaxValidationResult.failure(errors, suggestions)

    def quick_check(self, code: str) -> tuple[bool, list[str]]:
        """快速检查代码是否有明显的语法错误模式。

        Args:
            code: 待检查的代码

        Returns:
            (是否有错误, 错误列表)
        """
        errors = []

        for pattern, suggestion in self.HALLUCINATION_PATTERNS:
            matches = re.finditer(pattern, code, re.MULTILINE)
            for match in matches:
                line_num = code[: match.start()].count("\n") + 1
                line_text = code.splitlines()[line_num - 1] if line_num <= len(code.splitlines()) else ""
                errors.append(
                    f"Line {line_num}: Pattern '{match.group()}' detected. {suggestion} Found: '{line_text.strip()}'"
                )

        # Check for indentation inconsistency
        if self._has_indentation_issues(code):
            errors.append("Indentation appears inconsistent (mixing tabs and spaces, or non-4-space multiples)")

        return len(errors) == 0, errors

    def fix(self, code: str) -> tuple[str, list[HallucinationFix]]:
        """尝试自动修复代码中的LLM幻觉错误。

        Args:
            code: 待修复的代码

        Returns:
            (修复后的代码, 修复详情列表)
        """
        fixes = []
        fixed_code = code

        for pattern, replacement, explanation in self.FIX_PATTERNS:
            matches = list(re.finditer(pattern, fixed_code, re.MULTILINE))
            for match in matches:
                original = match.group()
                fixed = re.sub(pattern, replacement, original)
                if original != fixed:
                    line_num = fixed_code[: match.start()].count("\n") + 1
                    fixes.append(
                        HallucinationFix(
                            original=original, fixed=fixed, explanation=explanation, line=line_num, confidence=0.95
                        )
                    )
                    fixed_code = fixed_code.replace(original, fixed, 1)

        # 应用缩进修复
        fixed_code, indent_fixes = self._fix_indentation(fixed_code)
        fixes.extend(indent_fixes)

        return fixed_code, fixes

    def _fix_indentation(self, code: str) -> tuple[str, list[HallucinationFix]]:
        """修复缩进问题：Tab → 4空格，块结构缩进修正。

        使用栈跟踪当前块缩进级别，自动修复 body 行的缩进。

        Args:
            code: 待修复的代码

        Returns:
            (修复后的代码, 修复详情列表)
        """
        fixes: list[HallucinationFix] = []
        lines = code.split("\n")
        fixed_lines = []

        # 关键字后面跟冒号的行，会开启一个新块
        BLOCK_OPENERS = (r"^\s*(def|class|if|elif|else|for|while|try|except|finally|with|async\s+def)\b",)

        # 跟踪期望的缩进级别栈
        # 每个元素是 (indent_level, is_immediate_next_line)
        indent_stack: list[int] = [0]  # 起始缩进为0

        for line_num, line in enumerate(lines, 1):
            if not line.strip():
                fixed_lines.append(line)
                continue

            # Tab → 4空格
            if "\t" in line:
                indent_level = 0
                for ch in line:
                    if ch == "\t":
                        indent_level += 1
                    else:
                        break
                new_indent = "    " * indent_level
                rest = line.lstrip("\t")
                line = new_indent + rest
                fixes.append(
                    HallucinationFix(
                        original="\\t",
                        fixed=f"{new_indent}",
                        explanation="Tab converted to spaces",
                        line=line_num,
                        confidence=0.95,
                    )
                )

            leading_spaces = len(line) - len(line.lstrip())
            stripped = line.lstrip()

            # 检查是否是块开启行
            is_block_opener = bool(re.match(BLOCK_OPENERS[0], stripped))

            # 更新缩进栈
            if is_block_opener:
                # 块开启行本身缩进可能是正确的（顶层是0，嵌套是4的倍数）
                # 但如果是1-3空间，可能是错误的
                if 0 < leading_spaces < 4:
                    correct_indent = 4
                    line = " " * correct_indent + stripped
                    fixes.append(
                        HallucinationFix(
                            original=f"{leading_spaces} spaces",
                            fixed=f"{correct_indent} spaces",
                            explanation=f"Block opener indentation corrected to {correct_indent} spaces",
                            line=line_num,
                            confidence=0.9,
                        )
                    )
                    leading_spaces = correct_indent
                # 块开启后，下一行的期望缩进是 current + 4
                expected_next_indent = leading_spaces + 4
                indent_stack.append(expected_next_indent)
            else:
                # 非块开启行，检查是否匹配期望缩进
                expected_indent = indent_stack[-1] if indent_stack else 0

                # 如果当前缩进与期望不符，尝试修复
                if leading_spaces != expected_indent:
                    # 只修复：如果缩进 < 期望 且 差距是1-3（明显的错误）
                    # 或者 缩进 > 期望 但小于下一个期望（缺少缩进的情况）
                    if leading_spaces < expected_indent:
                        # 当前缩进小于期望，可能是整体偏移错误
                        # 检查是否整个文件的缩进都是错的
                        line_indent_delta = expected_indent - leading_spaces
                        if line_indent_delta <= 3:
                            # 可能是轻微的偏移错误，修正
                            line = " " * expected_indent + stripped
                            fixes.append(
                                HallucinationFix(
                                    original=f"{leading_spaces} spaces",
                                    fixed=f"{expected_indent} spaces",
                                    explanation=f"Body indentation corrected to match block (expected {expected_indent})",
                                    line=line_num,
                                    confidence=0.85,
                                )
                            )
                            leading_spaces = expected_indent

            fixed_lines.append(line)

        return "\n".join(fixed_lines), fixes

    def _has_indentation_issues(self, code: str) -> bool:
        """检查代码是否有缩进问题。"""
        lines = code.split("\n")
        has_tabs = False
        has_spaces = False
        inconsistent_indent = False

        for line in lines:
            if "\t" in line:
                has_tabs = True
            if "    " in line:  # 4 spaces
                has_spaces = True
            if has_tabs and has_spaces:
                return True  # Mixed indentation

            # Check if indentation is multiple of 4
            stripped = line.lstrip()
            if stripped and stripped != line:  # Has leading whitespace
                leading = line[: len(line) - len(stripped)]
                if len(leading) % 4 != 0:
                    inconsistent_indent = True

        return inconsistent_indent


@dataclass
class PostWriteVerification:
    """后验检查结果。"""

    success: bool
    expected: str
    actual: str | None = None
    error: str | None = None


def verify_written_code(
    filepath: str,
    expected_content: str,
    *,
    read_func: Callable[[str], str] | None = None,
) -> PostWriteVerification:
    """后验检查：验证写入的文件内容是否与预期一致。

    Args:
        filepath: 文件路径
        expected_content: 预期写入的内容
        read_func: 可选的读取函数（用于测试）

    Returns:
        PostWriteVerification 结果
    """
    if read_func is not None:
        try:
            actual = read_func(filepath)
        except Exception as e:
            return PostWriteVerification(
                success=False,
                expected=expected_content,
                error=f"Failed to read file: {e}",
            )
    else:
        try:
            with open(filepath, encoding="utf-8") as f:
                actual = f.read()
        except FileNotFoundError:
            return PostWriteVerification(
                success=False,
                expected=expected_content,
                actual=None,
                error="File not found",
            )
        except Exception as e:
            return PostWriteVerification(
                success=False,
                expected=expected_content,
                error=f"Failed to read file: {e}",
            )

    if actual == expected_content:
        return PostWriteVerification(
            success=True,
            expected=expected_content,
            actual=actual,
        )
    else:
        # 内容不匹配，计算差异位置
        expected_lines = expected_content.splitlines()
        actual_lines = actual.splitlines()
        diff_lines = []

        for i, (exp, act) in enumerate(zip(expected_lines, actual_lines)):
            if exp != act:
                diff_lines.append(f"Line {i + 1}: expected {exp!r}, got {act!r}")

        return PostWriteVerification(
            success=False,
            expected=expected_content[:200],
            actual=actual[:200] if actual else None,
            error=f"Content mismatch at {len(diff_lines)} line(s): " + "; ".join(diff_lines[:3]),
        )


def fix_code_with_tool(code: str, filepath: str | None) -> tuple[str, list[HallucinationFix]]:
    """使用第三方工具自动修复代码格式。

    Args:
        code: 待修复的代码
        filepath: 文件路径（用于确定语言类型）

    Returns:
        (修复后的代码, 修复详情列表)
    """
    fixes: list[HallucinationFix] = []
    if not filepath:
        return code, fixes

    ext = Path(filepath).suffix.lower()

    try:
        if ext == ".py":
            return _fix_python_with_ruff(code, fixes)
        elif ext in (".js", ".ts", ".jsx", ".tsx"):
            return _fix_js_ts_with_prettier(code, filepath, fixes)
        elif ext == ".go":
            return _fix_go_with_gofmt(code, fixes)
        elif ext == ".rs":
            return _fix_rust_with_rustfmt(code, fixes)
    except Exception as e:
        logger.warning("Auto-fix failed for %s: %s", filepath, e)

    return code, fixes


def _fix_python_with_ruff(code: str, fixes: list[HallucinationFix]) -> tuple[str, list[HallucinationFix]]:
    """使用 ruff 自动修复 Python 代码。

    Note: ruff check --fix 需要文件路径，不能从 stdin 读取。
    这里只使用 ruff format 来格式化代码。
    语法错误（如 return0）的修复由 PythonCodeValidator.fix() 处理。
    """
    try:
        # ruff format 可以从 stdin 读取并输出到 stdout
        result_fmt = subprocess.run(
            ["python", "-m", "ruff", "format", "-"],
            input=code,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result_fmt.stdout:
            fixed_code = result_fmt.stdout
            if fixed_code != code:
                fixes.append(
                    HallucinationFix(
                        original=code[:100],
                        fixed=fixed_code[:100],
                        explanation="Applied ruff formatting",
                        line=1,
                        confidence=0.95,
                    )
                )
            return fixed_code, fixes
    except FileNotFoundError:
        logger.debug("ruff not found, skipping Python auto-fix")
    except Exception as e:
        logger.warning("ruff auto-fix failed: %s", e)
    return code, fixes


def _fix_js_ts_with_prettier(
    code: str, filepath: str, fixes: list[HallucinationFix]
) -> tuple[str, list[HallucinationFix]]:
    """使用 prettier 自动修复 JS/TS 代码。"""
    try:
        # prettier filepath (without --write) outputs formatted code to stdout
        result = subprocess.run(
            ["npx", "prettier", filepath],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.stdout:
            fixed_code = result.stdout
            if fixed_code != code:
                fixes.append(
                    HallucinationFix(
                        original=code[:100],
                        fixed=fixed_code[:100],
                        explanation="Applied prettier formatting",
                        line=1,
                        confidence=0.95,
                    )
                )
            return fixed_code, fixes
    except FileNotFoundError:
        logger.debug("prettier not found, skipping JS/TS auto-fix")
    except Exception as e:
        logger.warning("prettier auto-fix failed: %s", e)
    return code, fixes


def _fix_go_with_gofmt(code: str, fixes: list[HallucinationFix]) -> tuple[str, list[HallucinationFix]]:
    """使用 gofmt 自动修复 Go 代码。"""
    try:
        # gofmt 需要文件路径，不能从 stdin 读取，使用临时文件
        with tempfile.NamedTemporaryFile(mode="w", suffix=".go", delete=False, encoding="utf-8") as f:
            f.write(code)
            f.flush()
            temp_path = f.name

        try:
            subprocess.run(
                ["gofmt", "-w", temp_path],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,  # gofmt returns non-zero if file is already formatted
            )
            with open(temp_path, encoding="utf-8") as f:
                fixed_code = f.read()
            if fixed_code != code:
                fixes.append(
                    HallucinationFix(
                        original=code[:100],
                        fixed=fixed_code[:100],
                        explanation="Applied gofmt formatting",
                        line=1,
                        confidence=0.95,
                    )
                )
            return fixed_code, fixes
        finally:
            Path(temp_path).unlink(missing_ok=True)
    except FileNotFoundError:
        logger.debug("gofmt not found, skipping Go auto-fix")
    except Exception as e:
        logger.warning("gofmt auto-fix failed: %s", e)
    return code, fixes


def _fix_rust_with_rustfmt(code: str, fixes: list[HallucinationFix]) -> tuple[str, list[HallucinationFix]]:
    """使用 rustfmt 自动修复 Rust 代码。"""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rs", delete=False, encoding="utf-8") as f:
            f.write(code)
            f.flush()
            temp_path = f.name

        try:
            # rustfmt outputs to stdout by default
            result = subprocess.run(
                ["rustfmt", temp_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            fixed_code = result.stdout
            if fixed_code and fixed_code != code:
                fixes.append(
                    HallucinationFix(
                        original=code[:100],
                        fixed=fixed_code[:100],
                        explanation="Applied rustfmt formatting",
                        line=1,
                        confidence=0.95,
                    )
                )
                return fixed_code, fixes
        finally:
            Path(temp_path).unlink(missing_ok=True)
    except FileNotFoundError:
        logger.debug("rustfmt not found, skipping Rust auto-fix")
    except Exception as e:
        logger.warning("rustfmt auto-fix failed: %s", e)
    return code, fixes


class MultiLanguageCodeValidator:
    """多语言代码验证器。"""

    def __init__(self):
        self.python_validator = PythonCodeValidator()
        self._validators = {
            ".py": lambda c, f: self.python_validator.validate(c, f),
            ".pyw": lambda c, f: self.python_validator.validate(c, f),
            ".js": lambda c, f: self._js_validator(c, f),
            ".ts": lambda c, f: self._ts_validator(c, f),
            ".jsx": lambda c, f: self._jsx_validator(c, f),
            ".tsx": lambda c, f: self._tsx_validator(c, f),
            ".go": lambda c, f: self._go_validator(c, f),
            ".rs": lambda c, f: self._rust_validator(c, f),
        }

    def validate(self, code: str, filepath: str | None = None) -> SyntaxValidationResult:
        """根据文件类型验证代码语法，自动修复可识别的格式错误。

        Args:
            code: 待验证的代码
            filepath: 文件路径（用于确定语言类型）

        Returns:
            语法验证结果
        """
        if not filepath:
            # 默认当作Python处理
            return self.python_validator.validate(code, filepath)

        # 第一步：尝试使用第三方工具自动修复
        fixed_code, fixes = fix_code_with_tool(code, filepath)
        if fixes:
            logger.info("Auto-fixed %s using third-party tool: %d fixes", filepath, len(fixes))

        # 第二步：验证修复后的代码
        # 根据扩展名选择验证器
        ext = self._get_extension(filepath)
        validator_fn = self._validators.get(ext)

        if validator_fn is None:
            # 未知语言，不验证
            logger.debug("No validator for extension: %s", ext)
            return SyntaxValidationResult.success(fixed_code=fixed_code if fixes else None)

        result = validator_fn(fixed_code, filepath)

        # 如果验证成功，返回修复后的代码（优先使用验证器修复的代码）
        if result.is_valid:
            if result.fixed_code is not None:
                return SyntaxValidationResult.success(fixed_code=result.fixed_code)
            if fixes:
                return SyntaxValidationResult.success(fixed_code=fixed_code)

        return result

    def _get_extension(self, filepath: str) -> str:
        """获取文件扩展名。"""
        # 处理路径分隔符
        filepath = filepath.replace("\\", "/")
        if "." in filepath:
            return "." + filepath.rsplit(".", 1)[1].lower()
        return ""

    # JS/TS 幻觉修复模式
    JS_FIX_PATTERNS: list[tuple[str, str, str]] = [
        # 缺少分号
        (r"(\w+)\s*\n\s*}", r"\1;\n}", "Missing semicolon at end of statement"),
        # function() {} 需要空格
        (r"\bfunction\s*\(", r"function (", "Missing space after 'function'"),
        # 箭头函数格式
        (r"=>\s*{", r" => {", "Arrow function block syntax"),
    ]

    def _js_validator(self, code: str, filepath: str | None) -> SyntaxValidationResult:
        """JavaScript验证器（括号检查 + 幻觉修复）。"""
        # 先尝试自动修复 JS 特有的幻觉模式
        fixed_code, fixes = self._fix_js_hallucinations(code)

        # 括号匹配检查
        errors = self._check_brackets(fixed_code, ["()", "[]", "{}"])
        if errors:
            return SyntaxValidationResult.failure(errors)

        # 如果有修复，返回修复后的代码
        if fixes:
            return SyntaxValidationResult.success(fixed_code=fixed_code)

        return SyntaxValidationResult.success()

    def _ts_validator(self, code: str, filepath: str | None) -> SyntaxValidationResult:
        """TypeScript验证器。"""
        return self._js_validator(code, filepath)

    def _jsx_validator(self, code: str, filepath: str | None) -> SyntaxValidationResult:
        """JSX验证器。"""
        return self._js_validator(code, filepath)

    def _tsx_validator(self, code: str, filepath: str | None) -> SyntaxValidationResult:
        """TSX验证器。"""
        return self._js_validator(code, filepath)

    def _fix_js_hallucinations(self, code: str) -> tuple[str, list[HallucinationFix]]:
        """修复 JS/TS 代码中的幻觉错误。"""
        fixes: list[HallucinationFix] = []
        fixed_code = code

        for pattern, replacement, explanation in self.JS_FIX_PATTERNS:
            matches = list(re.finditer(pattern, fixed_code, re.MULTILINE))
            for match in matches:
                original = match.group()
                fixed = re.sub(pattern, replacement, original)
                if original != fixed:
                    line_num = fixed_code[: match.start()].count("\n") + 1
                    fixes.append(
                        HallucinationFix(
                            original=original,
                            fixed=fixed,
                            explanation=explanation,
                            line=line_num,
                            confidence=0.85,
                        )
                    )
                    fixed_code = fixed_code.replace(original, fixed, 1)

        return fixed_code, fixes

    # Go 幻觉修复模式
    GO_FIX_PATTERNS: list[tuple[str, str, str]] = [
        # 缺少分号 (gofmt 会自动处理)
        # fmt.Println 缺少括号
        (r"\bfmt\.Println?\s+", r"fmt.Println(", "fmt.Print needs parentheses"),
    ]

    def _go_validator(self, code: str, filepath: str | None) -> SyntaxValidationResult:
        """Go语言验证器（括号检查 + 幻觉修复）。"""
        # 尝试自动修复
        fixed_code, fixes = self._fix_go_hallucinations(code)

        # 括号匹配检查
        errors = self._check_brackets(fixed_code, ["()", "[]", "{}"])
        if errors:
            return SyntaxValidationResult.failure(errors)

        if fixes:
            return SyntaxValidationResult.success(fixed_code=fixed_code)

        return SyntaxValidationResult.success()

    def _fix_go_hallucinations(self, code: str) -> tuple[str, list[HallucinationFix]]:
        """修复 Go 代码中的幻觉错误。"""
        fixes: list[HallucinationFix] = []
        fixed_code = code

        for pattern, replacement, explanation in self.GO_FIX_PATTERNS:
            matches = list(re.finditer(pattern, fixed_code, re.MULTILINE))
            for match in matches:
                original = match.group()
                fixed = re.sub(pattern, replacement, original)
                if original != fixed:
                    line_num = fixed_code[: match.start()].count("\n") + 1
                    fixes.append(
                        HallucinationFix(
                            original=original,
                            fixed=fixed,
                            explanation=explanation,
                            line=line_num,
                            confidence=0.85,
                        )
                    )
                    fixed_code = fixed_code.replace(original, fixed, 1)

        return fixed_code, fixes

    # Rust 幻觉修复模式
    RUST_FIX_PATTERNS: list[tuple[str, str, str]] = [
        # println! 缺少感叹号
        (r"\bprintln\s*\(", r"println!(", "println! is a macro, use println!()"),
        # print! 缺少感叹号
        (r"\bprint\s*\(", r"print!(", "print! is a macro, use print!()"),
    ]

    def _rust_validator(self, code: str, filepath: str | None) -> SyntaxValidationResult:
        """Rust语言验证器（括号检查 + 幻觉修复）。"""
        # 尝试自动修复
        fixed_code, fixes = self._fix_rust_hallucinations(code)

        # 括号匹配检查
        errors = self._check_brackets(fixed_code, ["()", "[]", "{}", "<>"])
        if errors:
            return SyntaxValidationResult.failure(errors)

        if fixes:
            return SyntaxValidationResult.success(fixed_code=fixed_code)

        return SyntaxValidationResult.success()

    def _fix_rust_hallucinations(self, code: str) -> tuple[str, list[HallucinationFix]]:
        """修复 Rust 代码中的幻觉错误。"""
        fixes: list[HallucinationFix] = []
        fixed_code = code

        for pattern, replacement, explanation in self.RUST_FIX_PATTERNS:
            matches = list(re.finditer(pattern, fixed_code, re.MULTILINE))
            for match in matches:
                original = match.group()
                fixed = re.sub(pattern, replacement, original)
                if original != fixed:
                    line_num = fixed_code[: match.start()].count("\n") + 1
                    fixes.append(
                        HallucinationFix(
                            original=original,
                            fixed=fixed,
                            explanation=explanation,
                            line=line_num,
                            confidence=0.85,
                        )
                    )
                    fixed_code = fixed_code.replace(original, fixed, 1)

        return fixed_code, fixes

    def _check_brackets(self, code: str, bracket_pairs: list[str]) -> list[CodeSyntaxError]:
        """检查括号匹配。"""
        errors = []
        stack: list[tuple[str, int, int]] = []  # (char, position, line_num)
        bracket_map = {p[0]: p[1] for p in bracket_pairs}
        reverse_map = {p[1]: p[0] for p in bracket_pairs}

        for i, char in enumerate(code):
            line_num = code[:i].count("\n") + 1
            if char in bracket_map:
                stack.append((char, i, line_num))
            elif char in reverse_map:
                if not stack:
                    errors.append(
                        CodeSyntaxError(
                            line=line_num,
                            column=i,
                            message=f"Unexpected closing bracket '{char}'",
                            error_type="BracketError",
                        )
                    )
                else:
                    open_char, _, _ = stack.pop()
                    if bracket_map.get(open_char) != char:
                        errors.append(
                            CodeSyntaxError(
                                line=line_num,
                                column=i,
                                message=f"Mismatched bracket: '{open_char}' and '{char}'",
                                error_type="BracketError",
                            )
                        )

        for char, pos, line_num in stack:
            errors.append(
                CodeSyntaxError(
                    line=line_num,
                    column=pos,
                    message=f"Unclosed bracket '{char}'",
                    error_type="BracketError",
                )
            )

        return errors


# 全局验证器实例
_code_validator = MultiLanguageCodeValidator()


def validate_code_syntax(code: str, filepath: str | None = None) -> SyntaxValidationResult:
    """验证代码语法的便捷函数。

    Args:
        code: 待验证的代码
        filepath: 文件路径

    Returns:
        语法验证结果
    """
    return _code_validator.validate(code, filepath)


def format_validation_error(result: SyntaxValidationResult, filepath: str | None = None) -> str:
    """格式化验证错误为用户友好的错误消息。

    Args:
        result: 验证结果
        filepath: 文件路径

    Returns:
        格式化的错误消息
    """
    if result.is_valid:
        return ""

    if not result.errors:
        return "Code validation failed"

    error_parts = []
    for error in result.errors:
        if filepath:
            error_parts.append(f"{filepath}:{error.line}: {error.error_type}: {error.message}")
        else:
            error_parts.append(f"Line {error.line}: {error.error_type}: {error.message}")

    message = "\n".join(error_parts)

    if result.suggestions:
        message += "\n\nSuggestions:\n" + "\n".join(f"  - {s}" for s in result.suggestions)

    return message


__all__ = [
    "CodeSyntaxError",
    "HallucinationFix",
    "MultiLanguageCodeValidator",
    "PythonCodeValidator",
    "SyntaxValidationResult",
    "fix_code_with_tool",
    "format_validation_error",
    "validate_code_syntax",
]
