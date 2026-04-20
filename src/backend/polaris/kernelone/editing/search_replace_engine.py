"""Native search/replace engine for resilient single-file edits.

集成 10 种匹配策略:
1. 精确匹配 (unique window)
2. 空白规范化匹配
3. 前导空白偏移匹配
4. 省略号锚点匹配 (...)
5. 相对缩进匹配
6. DMP (diff-match-patch) 匹配
7. DMP 行级匹配
8. 序列匹配 (SequenceMatcher)
9. 字符级幻觉修复匹配
10. 组合预处理匹配
"""

from __future__ import annotations

import difflib
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

try:
    from diff_match_patch import diff_match_patch  # type: ignore
except (RuntimeError, ValueError, ImportError):  # pragma: no cover - optional dependency
    diff_match_patch = None

# 集成 precise_matcher 的字符级幻觉修复
from polaris.kernelone.tool_execution.suggestions.precise_matcher import (
    fuzzy_replace,
)


class RelativeIndenter:
    """Encode absolute indentation into relative indentation deltas."""

    def __init__(self, texts: Iterable[str]) -> None:
        corpus = "".join(texts)
        candidates = ["__HP_OUTDENT__", "__HP_REL_OUTDENT__", "__HP_OD__"]
        marker = next((token for token in candidates if token not in corpus), None)
        if marker is None:
            marker = f"__HP_OUTDENT_{abs(hash(corpus)) % 1000000}__"
        self.marker = marker

    def make_relative(self, text: str) -> str:
        if self.marker in text:
            raise ValueError(f"text already contains marker: {self.marker}")
        lines = text.splitlines(keepends=True)
        output: list[str] = []
        prev_indent = ""
        for line in lines:
            body = line.rstrip("\r\n")
            indent_len = len(body) - len(body.lstrip())
            indent = line[:indent_len]
            change = indent_len - len(prev_indent)
            if change > 0:
                prefix = indent[-change:]
            elif change < 0:
                prefix = self.marker * (-change)
            else:
                prefix = ""
            output.append(prefix + "\n" + line[indent_len:])
            prev_indent = indent
        return "".join(output)

    def make_absolute(self, text: str) -> str:
        lines = text.splitlines(keepends=True)
        if len(lines) % 2 != 0:
            raise ValueError("relative indentation payload is malformed")
        output: list[str] = []
        prev_indent = ""
        for i in range(0, len(lines), 2):
            dent = lines[i].rstrip("\r\n")
            non_indent = lines[i + 1]
            if dent.startswith(self.marker):
                outdent_len = len(dent) // len(self.marker)
                cur_indent = prev_indent[:-outdent_len] if outdent_len else prev_indent
            else:
                cur_indent = prev_indent + dent

            if non_indent.rstrip("\r\n"):
                output.append(cur_indent + non_indent)
            else:
                output.append(non_indent)
            prev_indent = cur_indent
        merged = "".join(output)
        if self.marker in merged:
            raise ValueError("failed to restore absolute indentation")
        return merged


def _ensure_trailing_newline(text: str) -> str:
    if text.endswith("\n") or text.endswith("\r\n"):
        return text
    return text + "\n"


def _line_normalize_for_match(lines: Iterable[str]) -> list[str]:
    return [ln.rstrip() for ln in lines]


def _strip_blank_edge(text: str) -> str:
    return text.strip("\n") + "\n"


def _reverse_lines(text: str) -> str:
    lines = text.splitlines(keepends=True)
    lines.reverse()
    return "".join(lines)


def _replace_unique_window(
    original_text: str,
    search_text: str,
    replace_text: str,
    *,
    strip_ws: bool,
) -> str | None:
    src_lines = original_text.splitlines(keepends=True)
    needle_lines = search_text.splitlines(keepends=True)
    repl_lines = replace_text.splitlines(keepends=True)
    if not needle_lines:
        return None

    if strip_ws:
        src_cmp = _line_normalize_for_match(src_lines)
        needle_cmp = _line_normalize_for_match(needle_lines)
    else:
        src_cmp = list(src_lines)
        needle_cmp = list(needle_lines)

    width = len(needle_cmp)
    starts: list[int] = []
    for i in range(len(src_cmp) - width + 1):
        if src_cmp[i : i + width] == needle_cmp:
            starts.append(i)
    if len(starts) != 1:
        return None

    i = starts[0]
    merged = src_lines[:i] + repl_lines + src_lines[i + width :]
    return "".join(merged)


def _leading_whitespace_offset_apply(
    original_text: str,
    search_text: str,
    replace_text: str,
) -> str | None:
    src_lines = original_text.splitlines(keepends=True)
    part_lines = search_text.splitlines(keepends=True)
    repl_lines = replace_text.splitlines(keepends=True)
    if not src_lines or not part_lines:
        return None

    leading_widths = [len(line) - len(line.lstrip()) for line in (part_lines + repl_lines) if line.strip()]
    if leading_widths:
        min_width = min(leading_widths)
        if min_width:
            trim = min_width
            part_lines = [line[trim:] if line.strip() else line for line in part_lines]
            repl_lines = [line[trim:] if line.strip() else line for line in repl_lines]

    width = len(part_lines)
    for i in range(len(src_lines) - width + 1):
        chunk = src_lines[i : i + width]
        if not all(chunk[j].lstrip() == part_lines[j].lstrip() for j in range(width)):
            continue

        offsets = {chunk[j][: len(chunk[j]) - len(part_lines[j])] for j in range(width) if chunk[j].strip()}
        if len(offsets) != 1:
            continue
        prefix = next(iter(offsets))
        patched = [prefix + line if line.strip() else line for line in repl_lines]
        return "".join(src_lines[:i] + patched + src_lines[i + width :])

    return None


def _try_dotdot_ellipsis(content: str, search: str, replace: str) -> str | None:
    pattern = re.compile(r"^\s*\.\.\.\s*$")
    search_lines = search.splitlines(keepends=True)
    replace_lines = replace.splitlines(keepends=True)
    dot_search = [i for i, line in enumerate(search_lines) if pattern.match(line.strip())]
    dot_replace = [i for i, line in enumerate(replace_lines) if pattern.match(line.strip())]
    if not dot_search and not dot_replace:
        return None
    if len(dot_search) != len(dot_replace):
        return None

    # Require same separator lines to keep deterministic behavior.
    for s_idx, r_idx in zip(dot_search, dot_replace):
        if search_lines[s_idx].strip() != replace_lines[r_idx].strip():
            return None

    def _split_segments(lines: list[str], splits: list[int]) -> list[str]:
        parts: list[str] = []
        start = 0
        for split in splits:
            parts.append("".join(lines[start:split]))
            start = split + 1
        parts.append("".join(lines[start:]))
        return parts

    search_parts = _split_segments(search_lines, dot_search)
    replace_parts = _split_segments(replace_lines, dot_replace)

    current = content
    for search_part, replace_part in zip(search_parts, replace_parts):
        if not search_part and not replace_part:
            continue
        if not search_part and replace_part:
            if not current.endswith("\n"):
                current += "\n"
            current += replace_part
            continue
        count = current.count(search_part)
        if count != 1:
            return None
        current = current.replace(search_part, replace_part, 1)
    return current


def _dmp_apply(search_text: str, replace_text: str, original_text: str) -> str | None:
    if diff_match_patch is None:
        return None
    dmp = diff_match_patch()
    dmp.Diff_Timeout = 3
    diff = dmp.diff_main(search_text, replace_text, None)
    dmp.diff_cleanupSemantic(diff)
    dmp.diff_cleanupEfficiency(diff)
    patches = dmp.patch_make(search_text, diff)
    # Remap patch offsets from search-space to original-space (Aider-style).
    diff_s_o = dmp.diff_main(search_text, original_text)
    for patch in patches:
        patch.start1 = dmp.diff_xIndex(diff_s_o, patch.start1)
        patch.start2 = dmp.diff_xIndex(diff_s_o, patch.start2)
    updated, success = dmp.patch_apply(patches, original_text)
    return updated if all(success) else None


def _dmp_lines_apply(search_text: str, replace_text: str, original_text: str) -> str | None:
    if diff_match_patch is None:
        return None
    if not (search_text.endswith("\n") and replace_text.endswith("\n") and original_text.endswith("\n")):
        return None

    dmp = diff_match_patch()
    dmp.Diff_Timeout = 5
    dmp.Match_Threshold = 0.1
    dmp.Match_Distance = 100000
    dmp.Match_MaxBits = 32
    dmp.Patch_Margin = 1

    all_text = search_text + replace_text + original_text
    all_lines, _, mapping = dmp.diff_linesToChars(all_text, "")

    search_num = len(search_text.splitlines())
    replace_num = len(replace_text.splitlines())
    original_num = len(original_text.splitlines())

    search_lines = all_lines[:search_num]
    replace_lines = all_lines[search_num : search_num + replace_num]
    original_lines = all_lines[search_num + replace_num : search_num + replace_num + original_num]

    diff_lines = dmp.diff_main(search_lines, replace_lines, None)
    dmp.diff_cleanupSemantic(diff_lines)
    dmp.diff_cleanupEfficiency(diff_lines)
    patches = dmp.patch_make(search_lines, diff_lines)
    new_lines, success = dmp.patch_apply(patches, original_lines)
    if not all(success):
        return None
    return "".join(mapping[ord(ch)] for ch in new_lines)


def _relative_indent_apply(
    search_text: str,
    replace_text: str,
    original_text: str,
) -> str | None:
    indenter = RelativeIndenter([search_text, replace_text, original_text])
    rel_search = indenter.make_relative(_ensure_trailing_newline(search_text))
    rel_replace = indenter.make_relative(_ensure_trailing_newline(replace_text))
    rel_original = indenter.make_relative(_ensure_trailing_newline(original_text))
    rel_updated = _replace_unique_window(
        rel_original,
        rel_search,
        rel_replace,
        strip_ws=False,
    )
    if rel_updated is None:
        return None
    try:
        return indenter.make_absolute(rel_updated)
    except ValueError:
        return None


def _sequence_match_apply(
    search_text: str,
    replace_text: str,
    original_text: str,
) -> str | None:
    needle = search_text.strip()
    if not needle:
        return None
    hay = original_text
    blocks = list(difflib.SequenceMatcher(None, hay, needle).get_matching_blocks())
    if not blocks:
        return None
    best = max(blocks, key=lambda block: block.size)
    if best.size < max(8, len(needle) // 3):
        return None
    start = max(0, best.a - (best.b if best.b > 0 else 0))
    end = min(len(hay), start + len(search_text))
    candidate = hay[start:end]
    if not candidate:
        return None
    if hay.count(candidate) != 1:
        return None
    return hay.replace(candidate, replace_text, 1)


def _strategy_search_and_replace(search_text: str, replace_text: str, original_text: str) -> str | None:
    if not search_text:
        return None
    if original_text.count(search_text) == 0:
        return None
    return original_text.replace(search_text, replace_text, 1)


def _try_strategy_with_preproc(
    *,
    search_text: str,
    replace_text: str,
    original_text: str,
    strategy,
    strip_blank: bool,
    relative_indent: bool,
    reverse_lines: bool,
) -> str | None:
    s = search_text
    r = replace_text
    o = original_text
    indenter: RelativeIndenter | None = None

    if strip_blank:
        s = _strip_blank_edge(s)
        r = _strip_blank_edge(r)
        o = _strip_blank_edge(o)

    if relative_indent:
        indenter = RelativeIndenter([s, r, o])
        s = indenter.make_relative(_ensure_trailing_newline(s))
        r = indenter.make_relative(_ensure_trailing_newline(r))
        o = indenter.make_relative(_ensure_trailing_newline(o))

    if reverse_lines:
        s = _reverse_lines(s)
        r = _reverse_lines(r)
        o = _reverse_lines(o)

    res = strategy(s, r, o)
    if res is None:
        return None

    if reverse_lines:
        res = _reverse_lines(res)

    if relative_indent and indenter is not None:
        try:
            res = indenter.make_absolute(res)
        except ValueError:
            return None

    return res


def _precise_matcher_apply(
    search_text: str,
    replace_text: str,
    original_text: str,
) -> str | None:
    """使用 precise_matcher 进行字符级幻觉修复匹配。

    处理常见的 LLM 字符级错误:
    - return0 -> return 0
    - returnNone -> return None
    - if( -> if (
    """
    result, metadata = fuzzy_replace(original_text, search_text, replace_text)
    if metadata.get("success"):
        return result
    return None


def apply_fuzzy_search_replace(
    *,
    content: str,
    search: str,
    replace: str,
) -> str | None:
    """Apply fuzzy replacement when exact match is unavailable.

    按优先级尝试 10 种匹配策略:
    1. 精确匹配 (unique window)
    2. 空白规范化匹配
    3. 前导空白偏移匹配
    4. 省略号锚点匹配 (...)
    5. 相对缩进匹配
    6. 字符级幻觉修复 (precise_matcher)
    7. DMP (diff-match-patch) 匹配
    8. DMP 行级匹配
    9. 组合预处理匹配
    10. 序列匹配 (SequenceMatcher)
    """
    if not content or not search:
        return None

    exact = _replace_unique_window(content, search, replace, strip_ws=False)
    if exact is not None:
        return exact

    ws = _replace_unique_window(content, search, replace, strip_ws=True)
    if ws is not None:
        return ws

    leading_ws = _leading_whitespace_offset_apply(content, search, replace)
    if leading_ws is not None:
        return leading_ws

    dotdot = _try_dotdot_ellipsis(content, search, replace)
    if dotdot is not None:
        return dotdot

    rel = _relative_indent_apply(search, replace, content)
    if rel is not None:
        return rel

    # 字符级幻觉修复 (precise_matcher)
    precise = _precise_matcher_apply(search, replace, content)
    if precise is not None:
        return precise

    dmp_res = _dmp_apply(search, replace, content)
    if dmp_res is not None:
        return dmp_res

    preprocs = [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, False),
        (False, False, True),
        (True, False, True),
    ]
    strategies = [
        _strategy_search_and_replace,
        _dmp_lines_apply,
    ]
    for strategy in strategies:
        for strip_blank, rel_indent_flag, reverse_flag in preprocs:
            result = _try_strategy_with_preproc(
                search_text=search,
                replace_text=replace,
                original_text=content,
                strategy=strategy,
                strip_blank=strip_blank,
                relative_indent=rel_indent_flag,
                reverse_lines=reverse_flag,
            )
            if result is not None:
                return result

    return _sequence_match_apply(search, replace, content)


def apply_edit_with_metadata(
    *,
    content: str,
    search: str,
    replace: str,
) -> tuple[str | None, dict[str, Any]]:
    """应用编辑并返回详细的元数据。

    Returns:
        (新内容, 元数据字典)
        元数据包含: strategy_used, similarity, fixes_applied 等
    """
    metadata: dict[str, Any] = {"success": False, "strategy_used": None}

    if not content or not search:
        metadata["error"] = "Empty content or search"
        return None, metadata

    # 尝试 precise_matcher 获取详细元数据
    result, pm_metadata = fuzzy_replace(content, search, replace)
    if pm_metadata.get("success"):
        metadata["success"] = True
        metadata["strategy_used"] = "precise_matcher"
        metadata.update(pm_metadata)
        return result, metadata

    # 回退到标准模糊匹配
    fuzzy_result = apply_fuzzy_search_replace(content=content, search=search, replace=replace)
    if fuzzy_result is not None:
        metadata["success"] = True
        metadata["strategy_used"] = "fuzzy_search_replace"
        return fuzzy_result, metadata

    metadata["error"] = "No match found with any strategy"
    return None, metadata
