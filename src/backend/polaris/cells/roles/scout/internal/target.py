"""Target descriptor normalization for roles.scout (UTF-8)."""

from __future__ import annotations

import re

from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1

_STOPWORDS = frozenset(
    {
        "the",
        "is",
        "in",
        "a",
        "an",
        "of",
        "to",
        "for",
        "and",
        "or",
        "where",
        "what",
        "how",
        "why",
        "this",
        "that",
        "it",
        "on",
        "at",
        "by",
    }
)
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


def extract_terms(target: ScoutProbeTargetV1) -> list[str]:
    """Lowercased, de-duplicated, stopword-filtered search terms (order preserved)."""
    seen: set[str] = set()
    terms: list[str] = []
    for raw in _TOKEN_RE.findall(str(target.query)):
        token = raw.lower()
        if len(token) < 3 or token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    for sym in _as_str_list(target.hints.get("symbols")):
        low = sym.lower()
        if low not in seen:
            seen.add(low)
            terms.append(low)
    return terms


def hint_paths(target: ScoutProbeTargetV1) -> list[str]:
    return _as_str_list(target.hints.get("paths"))


def hint_globs(target: ScoutProbeTargetV1) -> list[str]:
    return _as_str_list(target.hints.get("globs"))


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]
