"""Shared contracts for content-type-aware deterministic crushers (T2-B).

These crushers implement headroom-style content-type-aware compression
*without* an LLM: they exploit the structure of common tool outputs (JSON,
logs, diffs, search results) to shrink token count deterministically and with
zero latency.

Design constraints:
    - KernelOne-only: no Polaris business semantics, no target-project code.
    - All text I/O uses explicit UTF-8 encoding.
    - 100% type annotations, complete docstrings, mypy clean.
    - Tokenizer-validated (fail-closed): every crusher must only return crushed
      text when ``crushed_tokens < original_tokens``; otherwise it returns the
      original unchanged (headroom's "reject if not smaller" rule). This is
      enforced centrally by :func:`finalize` so no individual crusher can leak
      an expanding result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from polaris.kernelone.context._token_estimator import estimate_tokens as _estimate_tokens

# Below this byte threshold crushing is skipped: the structural overhead of a
# crush marker is not worth it and small payloads rarely benefit.
MIN_CRUSH_BYTES: int = 512


class CrushKind(str, Enum):
    """The crusher that produced a :class:`CrushResult`.

    ``NONE`` means no crushing was applied (input too small, type unknown, or
    the crushed result was not actually smaller and was therefore rejected).
    """

    NONE = "none"
    JSON = "json"
    LOG = "log"
    DIFF = "diff"
    SEARCH = "search"


@dataclass(frozen=True)
class CrushResult:
    """Result of a deterministic crush pass.

    Attributes:
        text: The (possibly crushed) text. Equals the input verbatim when
            ``kind`` is :attr:`CrushKind.NONE`.
        original_tokens: Token estimate of the original input.
        crushed_tokens: Token estimate of :attr:`text`.
        ratio: ``crushed_tokens / original_tokens`` (1.0 when no shrink / empty
            input). Always in ``[0.0, 1.0]``.
        kind: Which crusher produced this result (or NONE).
    """

    text: str
    original_tokens: int
    crushed_tokens: int
    ratio: float
    kind: CrushKind


def estimate_tokens(text: str) -> int:
    """Estimate tokens via the canonical ContextOS estimator.

    Single source of truth -- do not duplicate the estimation formula here.

    Args:
        text: Text to estimate.

    Returns:
        Estimated token count (>= 0).
    """
    return _estimate_tokens(text)


def no_op(text: str) -> CrushResult:
    """Build a NONE result that returns the input verbatim.

    Args:
        text: The original text.

    Returns:
        A :class:`CrushResult` with ``kind=NONE`` and ``ratio=1.0``.
    """
    tokens = estimate_tokens(text)
    return CrushResult(
        text=text,
        original_tokens=tokens,
        crushed_tokens=tokens,
        ratio=1.0,
        kind=CrushKind.NONE,
    )


def finalize(original: str, crushed: str, kind: CrushKind) -> CrushResult:
    """Tokenizer-validate a candidate crush and reject it if not smaller.

    This is the single enforcement point for headroom's "reject if not smaller"
    rule. A crusher proposes ``crushed``; we only accept it when it strictly
    reduces the token estimate, otherwise we fall back to the original text with
    ``kind=NONE``. This guarantees a crusher can never *expand* its input.

    Args:
        original: The original (uncrushed) text.
        crushed: The candidate crushed text.
        kind: The crusher kind to attribute on success.

    Returns:
        A :class:`CrushResult`: the crushed text with ``kind`` when it is
        strictly smaller, otherwise the original text with ``kind=NONE``.
    """
    original_tokens = estimate_tokens(original)
    crushed_tokens = estimate_tokens(crushed)

    if crushed_tokens < original_tokens:
        ratio = crushed_tokens / original_tokens if original_tokens > 0 else 1.0
        return CrushResult(
            text=crushed,
            original_tokens=original_tokens,
            crushed_tokens=crushed_tokens,
            ratio=ratio,
            kind=kind,
        )

    # Not smaller -> reject; return the original unchanged.
    return CrushResult(
        text=original,
        original_tokens=original_tokens,
        crushed_tokens=original_tokens,
        ratio=1.0,
        kind=CrushKind.NONE,
    )


__all__ = [
    "MIN_CRUSH_BYTES",
    "CrushKind",
    "CrushResult",
    "estimate_tokens",
    "finalize",
    "no_op",
]
