"""Pure lexical text cleanup with opaque double-quoted data boundaries."""

from __future__ import annotations

import re

_QUOTED_DATA = r'"(?:\\[\s\S]|[^"\\])*(?:"|\\?\Z)'
_QUOTED_RE = re.compile(_QUOTED_DATA)
_QUOTED_OR_TAG_RE = re.compile(
    _QUOTED_DATA
    + r"|</?(assistant|system|thinking|antthinking|tool_result|tool_call|analysis|final|user)(?:\s[^>]*)?>"
    + r"|</?[a-z][a-z0-9_-]{1,31}(?:\s[^>]*)?>",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_quoted_text(raw: str, *, strip_external_tags: bool = False, collapse_whitespace: bool = True) -> str:
    """Trim prose edges while preserving complete or incomplete quoted data.

    Escapes are consumed inside quotes; even a dangling escape or trailing
    whitespace in an incomplete string stays data. Optional tag removal only
    applies outside quotes and consumes an external tag with its attributes.
    Prose whitespace folds by default; callers with an edge-only trim policy
    can retain internal prose spacing with ``collapse_whitespace=False``.
    This neither validates JSON nor reconstructs a valid prefix.
    """
    parts: list[str] = []
    prose: list[str] = []
    end = 0
    tokens = _QUOTED_OR_TAG_RE if strip_external_tags else _QUOTED_RE
    for match in tokens.finditer(raw):
        prose.append(raw[end : match.start()])
        token = match.group()
        if token.startswith('"'):
            prefix = "".join(prose)
            if collapse_whitespace:
                prefix = _WHITESPACE_RE.sub(" ", prefix)
            parts.append(prefix if parts else prefix.lstrip())
            prose.clear()
            parts.append(token)
        else:
            prose.append(" ")
        end = match.end()
    prose.append(raw[end:])
    suffix = "".join(prose)
    if collapse_whitespace:
        suffix = _WHITESPACE_RE.sub(" ", suffix)
    parts.append(suffix.rstrip() if parts else suffix.strip())
    return "".join(parts)
