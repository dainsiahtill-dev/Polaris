"""Distill findings into a token-bounded summary pack (UTF-8).

P1: deterministic (zero-LLM). P2 adds an LLM-backed implementation behind the
same DistillerPort.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


class LLMDistiller:
    """DistillerPort backed by a cheap model; falls back to deterministic on error.

    ``call`` is an injected async function ``(prompt: str) -> str``. The
    production wiring binds it to a cheap model via ProviderManager (see
    build_default_scout_service). Keeping the deterministic path as the default
    factory means no provider config is required for the core probe pipeline.

    Note: real cheap-model provider wiring is a follow-up; currently
    ``build_default_scout_service`` keeps ``DeterministicDistiller`` as default
    to avoid bypassing ProviderManager (CLAUDE.md §7.3).
    """

    def __init__(self, call: Callable[[str], Awaitable[str]]) -> None:
        self._call = call
        self._fallback = DeterministicDistiller()

    async def distill(self, *, query: str, findings: list[ScoutFinding], token_budget: int) -> str:
        baseline = await self._fallback.distill(query=query, findings=findings, token_budget=token_budget)
        prompt = (
            f"Compress these code-search findings into <= {token_budget} tokens "
            f"answering: {query}\n\n{baseline}\n\nReturn only the distilled answer."
        )
        try:
            out = await self._call(prompt)
        except (RuntimeError, ValueError, TimeoutError):
            return baseline
        text = str(out or "").strip()
        return text or baseline
