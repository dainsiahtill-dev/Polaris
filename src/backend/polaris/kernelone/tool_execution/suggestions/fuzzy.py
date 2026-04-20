"""FuzzyMatchBuilder - 搜索未命中的模糊匹配建议。

当 LLM 的搜索字符串在文件中找不到时，提供：
1. 文件中最相似的行（使用 difflib.SequenceMatcher）
2. 相似度百分比
3. 字符级 diff（显示哪里不同）
4. 建议 LLM 先读取文件验证内容
"""

from __future__ import annotations

import difflib
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


# Python 语法错误预检测：检测 search 字符串中的典型拼写错误
_PYTHON_SYNTAX_ERRORS: list[tuple[str, str, str]] = [
    # (pattern_regex, error_msg, fix_msg)
    (
        r"\breturn0\b",
        "'return0' is invalid Python syntax. Did you mean 'return 0' (return + space + value)?",
        "'return 0'",
    ),
    (r"\breturnNone\b", "'returnNone' is invalid Python. Did you mean 'return None'?", "'return None'"),
    (r"\breturnTrue\b", "'returnTrue' is invalid Python. Did you mean 'return True'?", "'return True'"),
    (r"\breturnFalse\b", "'returnFalse' is invalid Python. Did you mean 'return False'?", "'return False'"),
    (r"\bif\s+not\s+(\w+)\s*:\s*return0\b", "'return0' inside if-block is invalid. Use 'return 0'.", "'return 0'"),
    (r"\bdef\s+\w+\([^)]*\)\s*->\s*\w+\s*:\s*return0\b", "'return0' is invalid. Use 'return 0'.", "'return 0'"),
    (r"\bprint0\b", "'print0' is invalid. Did you mean 'print()'?", "'print()'"),
    (r"\b(\w+)\s*\((\w+)\)\s*=\s*(\d+)(?!\s*[*/+-])", "Possible missing operator after number. Check syntax.", ""),
]


def _check_python_syntax_errors(search: str) -> str | None:
    """预检测 search 字符串中的 Python 语法错误。

    Returns:
        None if 无已知错误模式，否则返回具体错误描述
    """
    for pattern_regex, error_msg, _fix_msg in _PYTHON_SYNTAX_ERRORS:
        if re.search(pattern_regex, search, re.IGNORECASE):
            return error_msg
    return None


class FuzzyMatchBuilder:
    """为搜索未命中错误构建模糊匹配建议。"""

    name: str = "fuzzy_match"
    priority: int = 10

    def should_apply(self, error_result: dict[str, Any]) -> bool:
        error = str(error_result.get("error") or "").strip().lower()
        return error in ("no matches found", "no matches found.")

    def build(self, error_result: dict[str, Any], **kwargs: Any) -> str | None:
        content = error_result.get("content") or error_result.get("payload", {}).get("content", "")
        search = error_result.get("search") or error_result.get("args", {}).get("search", "")
        if not search:
            return None
        return _build_no_match_suggestion(content, search)


def _build_no_match_suggestion(content: str, search: str) -> str:
    """Build a helpful suggestion when search string is not found.

    Args:
        content: Full file content.
        search: The search string not found in content.

    Returns:
        A clear suggestion string showing what the LLM typed vs what's in the file.
    """
    search_str = str(search) if search else ""

    # Fix 1: Python syntax error pre-check (catches return0 early)
    syntax_error = _check_python_syntax_errors(search_str)
    if syntax_error:
        return (
            f"SYNTAX ERROR: {syntax_error} "
            f"Your search='{search_str[:200]!r}' is invalid Python. "
            "MUST use read_file() to copy the EXACT characters from source. "
            "Pay attention to spaces between keywords (e.g., 'return' + space + '0')."
            " Alternatively, use edit_file with line numbers to avoid string matching issues: "
            "edit_file(file='filepath', start_line=N, end_line=N, content='new_content')."
        )

    if not content or not search_str:
        return (
            f"Search='{search_str[:200]!r}' not found. Use read_file() to verify the exact content before editing. "
            "Consider using edit_file with line numbers: "
            "edit_file(file='filepath', start_line=N, end_line=N, content='new_content')."
        )

    file_lines = content.splitlines()
    if not file_lines:
        return (
            f"Search='{search_str[:200]!r}' not found. File is empty. Use read_file() to check file content. "
            "Consider using edit_file with line numbers: "
            "edit_file(file='filepath', start_line=N, end_line=N, content='new_content')."
        )

    search_normalized = str(search).replace("\\n", "\n").strip()
    search_parts = search_normalized.splitlines()
    n_search_lines = len(search_parts)

    # Find best matching line index
    best_ratio = 0.0
    best_idx = -1
    best_line = ""
    for i, line in enumerate(file_lines):
        ratio = difflib.SequenceMatcher(None, search_normalized, line).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = i
            best_line = line

    if best_ratio < 0.3:
        return (
            f"Search='{search_normalized[:200]!r}' not found. "
            "Use read_file() to verify the exact content. "
            f"File has {len(file_lines)} lines. "
            "Consider using edit_file with line numbers instead: "
            "edit_file(file='filepath', start_line=N, end_line=N, content='new_content')."
        )

    # For multi-line search: show ACTUAL consecutive lines starting from best_idx
    if n_search_lines > 1:
        actual_start = best_idx
        actual_end = min(best_idx + n_search_lines, len(file_lines))
        actual_lines = file_lines[actual_start:actual_end]

        # Build direct comparison: LLM search line vs actual file line
        parts = []
        for i, s_line in enumerate(search_parts):
            f_line = actual_lines[i] if i < len(actual_lines) else ""
            line_num = actual_start + i + 1
            s_stripped = s_line.strip()
            f_stripped = f_line.strip() if f_line else "<MISSING>"
            s_indent = len(s_line) - len(s_line.lstrip()) if s_line else 0
            f_indent = len(f_line) - len(f_line.lstrip()) if f_line else 0

            if s_stripped != f_stripped:
                # Content differs
                diff = f"CONTENT '{s_stripped}' -> '{f_stripped}'"
                if s_indent != f_indent:
                    parts.append(f"[L{line_num}] {s_indent}sp->{f_indent}sp: {diff}")
                else:
                    parts.append(f"[L{line_num}] {diff}")
            elif s_indent != f_indent:
                parts.append(f"[L{line_num}] indent:{s_indent}sp->{f_indent}sp: '{f_stripped}'")
            else:
                parts.append(f"[L{line_num}] '{f_stripped}' (match)")

        correct_file_snippet = " || ".join(repr(line) for line in actual_lines)
        hint = (
            f" PROBLEM: {n_search_lines}-line search does not match. "
            + " | ".join(parts)
            + f". CORRECT lines {actual_start + 1}-{actual_end}: {correct_file_snippet}"
        )
    else:
        # Single line
        s_indent = len(search_parts[0]) - len(search_parts[0].lstrip()) if search_parts else 0
        f_indent = len(best_line) - len(best_line.lstrip()) if best_line else 0
        if s_indent != f_indent:
            hint = (
                f" PROBLEM: indent {s_indent}sp -> should be {f_indent}sp. CORRECT line {best_idx + 1}: {best_line!r}"
            )
        else:
            hint = f" Line {best_idx + 1}: {best_line!r}."

    return (
        f"Search='{search_normalized[:200]!r}' NOT FOUND. "
        f"Best match at line {best_idx + 1} ({best_ratio:.0%} similar)."
        + hint
        + " Use read_file() to get exact file content. "
        + "Consider using edit_file with line numbers instead: "
        + "edit_file(file='filepath', start_line=N, end_line=N, content='new_content')."
    )
