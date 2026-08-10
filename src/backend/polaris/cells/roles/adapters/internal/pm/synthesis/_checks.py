"""Markdown deterministic-check extraction helpers for PM synthesis."""

from __future__ import annotations

import re

_DETERMINISTIC_CHECK_TOKEN_PATTERN = (
    r"(?:html|ts_syntax|js_syntax|py_compile|package_scripts|rust_compile|cpp_compile|java_compile|"
    r"go_compile|min_files:\d+|source_target_coverage:[^\s]+|content_any:[A-Za-z0-9_|-]+)"
)

_DETERMINISTIC_CHECK_RE = re.compile(rf"(?i)(?<![A-Za-z0-9_-])({_DETERMINISTIC_CHECK_TOKEN_PATTERN})(?![A-Za-z0-9_-])")

_DETERMINISTIC_CHECK_FULL_RE = re.compile(rf"(?i)^(?P<check>{_DETERMINISTIC_CHECK_TOKEN_PATTERN})$")

_CONTENT_ANY_RE = re.compile(r"(?i)content_any:([A-Za-z0-9_|-]+)")

_MARKDOWN_ATX_HEADING_RE = re.compile(r"^[ \t]{0,3}(?P<marks>#{1,6})[ \t]+(?P<title>.*?)[ \t]*$")

_MARKDOWN_FENCE_OPEN_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?P<list_marker>(?:[-+*])|(?:\d+[.)]))"
    r"(?P<list_spacing>[ \t]+)(?:\[[ xX]\][ \t]+)?)?"
    r"(?P<marker>`{3,}|~{3,})"
)

_MARKDOWN_FENCE_CLOSE_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>`{3,}|~{3,})[ \t]*$")

_MARKDOWN_LIST_CONTAINER_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<list_marker>(?:[-+*])|(?:\d+[.)]))(?P<list_spacing>[ \t]+)"
)

_MARKDOWN_LIST_ITEM_PREFIX_RE = re.compile(r"^(?:(?:[-+*])|(?:\d+[.)]))[ \t]+(?:\[[ xX]\][ \t]+)?")

_MARKDOWN_BLOCKQUOTE_RE = re.compile(r"^[ \t]{0,3}>[ \t]?(?P<content>.*)$")

_DETERMINISTIC_CHECK_SECTION_TITLES = frozenset(
    {
        "deterministic check",
        "deterministic checks",
        "确定性检查",
    }
)


def _dedupe_limited_texts(values: list[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        token = str(value or "").strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(token)
        if len(result) >= limit:
            break
    return result


def _normalize_markdown_atx_heading_title(value: str) -> str:
    title = re.sub(r"[ \t]+#+[ \t]*$", "", str(value or "")).strip()
    return title.rstrip(":：").strip().casefold()


def _markdown_lines_outside_fences(text: str) -> list[tuple[int, int, str, bool]]:
    lines: list[tuple[int, int, str, bool]] = []
    fence_char = ""
    fence_width = 0
    fence_close_indent_min = 0
    fence_close_indent_limit = 0
    list_container_stack: list[tuple[int, int]] = []
    blockquote_paragraph_active = False
    previous_line_blank = True
    offset = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        body_nonblank = bool(body.strip())
        indentation = len(body[: len(body) - len(body.lstrip(" \t"))].expandtabs(4))
        if fence_char and body_nonblank and fence_close_indent_min > 0 and indentation < fence_close_indent_min:
            fence_char = ""
            fence_width = 0
            fence_close_indent_min = 0
            fence_close_indent_limit = 0
        if fence_char:
            close_match = _MARKDOWN_FENCE_CLOSE_RE.match(body)
            if close_match is not None:
                marker = str(close_match.group("marker") or "")
                closing_indentation = len(str(close_match.group("indent") or "").expandtabs(4))
                if (
                    marker[0] == fence_char
                    and len(marker) >= fence_width
                    and closing_indentation >= fence_close_indent_min
                    and closing_indentation <= fence_close_indent_limit
                ):
                    fence_char = ""
                    fence_width = 0
                    fence_close_indent_min = 0
                    fence_close_indent_limit = 0
            previous_line_blank = False
            offset += len(line)
            continue
        list_container_match = _MARKDOWN_LIST_CONTAINER_RE.match(body)
        blockquote_match = _MARKDOWN_BLOCKQUOTE_RE.match(body)
        heading_match = _MARKDOWN_ATX_HEADING_RE.match(body)
        open_match = _MARKDOWN_FENCE_OPEN_RE.match(body)
        valid_fence_open = False
        if open_match is not None:
            marker = str(open_match.group("marker") or "")
            info_string = body[open_match.end() :]
            valid_fence_open = marker[0] != "`" or "`" not in info_string
        list_continuation = False
        if list_container_match is not None:
            list_marker_indent = len(str(list_container_match.group("indent") or "").expandtabs(4))
            while list_container_stack and list_marker_indent <= list_container_stack[-1][0]:
                list_container_stack.pop()
            list_content_indent = len(body[: list_container_match.end("list_spacing")].expandtabs(4))
            list_container_stack.append((list_marker_indent, list_content_indent))
        elif body_nonblank:
            if previous_line_blank or heading_match is not None or blockquote_match is not None or valid_fence_open:
                while list_container_stack and indentation < list_container_stack[-1][1]:
                    list_container_stack.pop()
            list_continuation = bool(list_container_stack)
        blockquote_continuation = False
        if not body_nonblank:
            blockquote_paragraph_active = False
        elif blockquote_match is not None:
            blockquote_paragraph_active = bool(str(blockquote_match.group("content") or "").strip())
        elif list_container_match is not None or heading_match is not None or valid_fence_open:
            blockquote_paragraph_active = False
        elif blockquote_paragraph_active:
            blockquote_continuation = True
        active_list_content_indent = list_container_stack[-1][1] if list_container_stack else 0
        if valid_fence_open and open_match is not None:
            marker = str(open_match.group("marker") or "")
            fence_char = marker[0]
            fence_width = len(marker)
            opening_indentation = len(str(open_match.group("indent") or "").expandtabs(4))
            if open_match.group("list_marker") is not None or (
                active_list_content_indent and opening_indentation >= active_list_content_indent
            ):
                fence_close_indent_min = active_list_content_indent
                fence_close_indent_limit = active_list_content_indent + 3
            elif opening_indentation >= 4:
                list_container_stack.clear()
                fence_close_indent_min = opening_indentation
                fence_close_indent_limit = opening_indentation + 3
            else:
                list_container_stack.clear()
                fence_close_indent_min = 0
                fence_close_indent_limit = 3
            previous_line_blank = False
            offset += len(line)
            continue
        lines.append(
            (
                offset,
                offset + len(line),
                body,
                list_continuation or blockquote_continuation,
            )
        )
        previous_line_blank = not body_nonblank
        offset += len(line)
    return lines


def _markdown_atx_headings(text: str) -> list[tuple[int, int, int, str]]:
    headings: list[tuple[int, int, int, str]] = []
    for line_start, line_end, body, _is_container_continuation in _markdown_lines_outside_fences(text):
        heading_match = _MARKDOWN_ATX_HEADING_RE.match(body)
        if heading_match is None:
            continue
        marks = str(heading_match.group("marks") or "")
        title = _normalize_markdown_atx_heading_title(str(heading_match.group("title") or ""))
        headings.append((len(marks), line_start, line_end, title))
    return headings


def _extract_declared_deterministic_checks(section_text: str) -> list[str]:
    values: list[str] = []
    previous_line_blank = True
    previous_bare_check = False
    for _line_start, _line_end, body, is_container_continuation in _markdown_lines_outside_fences(section_text):
        if not body.strip():
            previous_line_blank = True
            previous_bare_check = False
            continue
        if _MARKDOWN_ATX_HEADING_RE.match(body) is not None:
            previous_line_blank = True
            previous_bare_check = False
            continue
        indentation = len(body[: len(body) - len(body.lstrip(" \t"))].expandtabs(4))
        if indentation >= 4:
            previous_line_blank = False
            previous_bare_check = False
            continue
        candidate = body.strip()
        list_prefix = _MARKDOWN_LIST_ITEM_PREFIX_RE.match(candidate)
        if list_prefix is not None:
            candidate = candidate[list_prefix.end() :].strip()
        if len(candidate) >= 2 and candidate.startswith("`") and candidate.endswith("`"):
            candidate = candidate[1:-1].strip()
        match = _DETERMINISTIC_CHECK_FULL_RE.fullmatch(candidate)
        current_bare_check = False
        if match is not None and (
            list_prefix is not None or (not is_container_continuation and (previous_line_blank or previous_bare_check))
        ):
            values.append(str(match.group("check") or "").strip())
            current_bare_check = list_prefix is None
        previous_line_blank = False
        previous_bare_check = current_bare_check
    return values


def _extract_deterministic_checks_from_directive(directive: str, *, limit: int = 8) -> list[str]:
    text = str(directive or "")
    headings = _markdown_atx_headings(text)
    section_scoped = False
    for index, (section_level, _section_start, section_body_start, title) in enumerate(headings):
        if title not in _DETERMINISTIC_CHECK_SECTION_TITLES:
            continue
        section_end = len(text)
        for next_level, next_start, _next_body_start, _next_title in headings[index + 1 :]:
            if next_level <= section_level:
                section_end = next_start
                break
        text = text[section_body_start:section_end]
        section_scoped = True
        break
    values = (
        _extract_declared_deterministic_checks(text)
        if section_scoped
        else [str(match.group(1) or "").strip() for match in _DETERMINISTIC_CHECK_RE.finditer(text)]
    )
    return _dedupe_limited_texts(
        values,
        limit=limit,
    )


def _extract_content_any_keywords_from_directive(directive: str, *, limit: int = 8) -> list[str]:
    values: list[str] = []
    for match in _CONTENT_ANY_RE.finditer(str(directive or "")):
        values.extend(part.strip().lower() for part in str(match.group(1) or "").split("|"))
    return _dedupe_limited_texts(values, limit=limit)
