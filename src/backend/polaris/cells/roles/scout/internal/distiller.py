"""Distill findings into a token-bounded summary pack (UTF-8).

P1: deterministic (zero-LLM). P2 adds an LLM-backed implementation behind the
same DistillerPort.
"""
from __future__ import annotations

from polaris.cells.roles.scout.public.contracts import ScoutFinding

_CHARS_PER_TOKEN = 4


class DeterministicDistiller:
    """Zero-cost DistillerPort: format the ranked findings as a compact list."""

    async def distill(self, *, query: str, findings: list[ScoutFinding], token_budget: int) -> str:
        char_budget = max(40, token_budget * _CHARS_PER_TOKEN)
        header = f"Scout findings for: {query}\n"
        lines: list[str] = []
        used = len(header)
        for f in findings:
            loc = f"{f.path}:{f.line}" if f.line is not None else f.path
            label = f"- {loc}"
            if f.symbol:
                label += f" [{f.symbol}]"
            label += f" — {f.snippet[:120]}"
            if used + len(label) + 1 > char_budget:
                break
            lines.append(label)
            used += len(label) + 1
        if not lines:
            return (header + "(no matching code found)")[:char_budget]
        return (header + "\n".join(lines))[:char_budget]
