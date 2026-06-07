"""Score, de-duplicate and cap reconnaissance findings (UTF-8)."""

from __future__ import annotations

import re
from dataclasses import replace

from polaris.cells.roles.scout.internal.target import extract_terms
from polaris.cells.roles.scout.public.contracts import ScoutFinding, ScoutProbeTargetV1

_DEF_RE = re.compile(r"\b(?:def|class|func|function|interface|type)\s+(\w+)")


def rank(findings: list[ScoutFinding], target: ScoutProbeTargetV1) -> list[ScoutFinding]:
    """Return de-duplicated findings sorted by descending relevance, capped."""
    terms = extract_terms(target)
    deduped: dict[tuple[str, int | None], ScoutFinding] = {}
    for f in findings:
        deduped.setdefault((f.path, f.line), f)

    scored: list[tuple[float, ScoutFinding]] = []
    for f in deduped.values():
        score, symbol = _score(f, terms)
        scored.append((score, replace(f, confidence=round(min(score, 1.0), 3), symbol=symbol or f.symbol)))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [f for _, f in scored[: target.max_findings]]


def _score(finding: ScoutFinding, terms: list[str]) -> tuple[float, str | None]:
    snippet = finding.snippet.lower()
    score = 0.0
    symbol: str | None = None

    match = _DEF_RE.search(finding.snippet)
    if match:
        symbol = match.group(1)
        score += 0.6  # a definition is more valuable than a mention

    for term in terms:
        if term in snippet:
            score += 0.2
        if symbol and term in symbol.lower():
            score += 0.3
        if term in finding.path.lower():
            score += 0.15

    return score, symbol
