"""Deterministic typeorm model normalization repair, carved verbatim."""

from __future__ import annotations

import re

from ._common import (
    _TS_CLASS_FIELD_DECL_RE,
    _TS_DECORATOR_LINE_RE,
    _TYPEORM_IMPORT_LINE_RE,
)


def _normalize_undeclared_typeorm_model_source(text: str) -> str:
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        if _TYPEORM_IMPORT_LINE_RE.match(raw_line):
            continue
        if _TS_DECORATOR_LINE_RE.match(raw_line):
            continue
        lines.append(_normalize_ts_class_field_initialization(raw_line))
    normalized = "\n".join(lines).strip() + "\n"
    return re.sub(r"\n{3,}", "\n\n", normalized)


def _normalize_ts_class_field_initialization(line: str) -> str:
    match = _TS_CLASS_FIELD_DECL_RE.match(line)
    if not match:
        return line
    indent = match.group("indent")
    name = match.group("name")
    optional = match.group("optional")
    type_text = str(match.group("type") or "").strip()
    if optional:
        return f"{indent}{name}?: {type_text};"
    lowered = type_text.lower()
    if "[]" in type_text:
        return f"{indent}{name}: unknown[] = [];"
    if lowered == "string":
        return f'{indent}{name}: string = "";'
    if lowered == "number":
        return f"{indent}{name}: number = 0;"
    if lowered == "boolean":
        return f"{indent}{name}: boolean = false;"
    if lowered == "date":
        return f"{indent}{name}: Date = new Date(0);"
    return f"{indent}{name}: unknown = null;"
