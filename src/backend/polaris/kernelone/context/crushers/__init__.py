"""Content-type-aware deterministic crushers (T2-B / headroom cross-pollination).

A no-LLM, deterministic pre-pass that shrinks structured tool outputs (JSON,
logs, diffs, search results) before any LLM summarization. Every crusher is
tokenizer-validated: it only returns crushed text when the result is strictly
smaller than the input (headroom's "reject if not smaller" rule), otherwise it
returns the input unchanged.

Public entry point: :func:`crush_by_type`.

防重复造轮子提示:
    - Token estimation MUST go through the canonical estimator in
      ``polaris.kernelone.context._token_estimator`` (re-exported here as
      ``estimate_tokens``). Do not copy the estimation formula.
    - Add a new content type by writing a ``crush_<type>`` function that returns
      ``finalize(original, crushed, CrushKind.<TYPE>)`` and wiring it into
      ``router.crush_by_type`` + ``router.detect_content_type``.
"""

from __future__ import annotations

from polaris.kernelone.context.crushers.base import (
    MIN_CRUSH_BYTES,
    CrushKind,
    CrushResult,
    estimate_tokens,
    finalize,
    no_op,
)
from polaris.kernelone.context.crushers.diff_crush import crush_diff
from polaris.kernelone.context.crushers.json_crush import crush_json
from polaris.kernelone.context.crushers.log_crush import crush_log
from polaris.kernelone.context.crushers.router import crush_by_type, detect_content_type
from polaris.kernelone.context.crushers.search_crush import crush_search

__all__ = [
    "MIN_CRUSH_BYTES",
    "CrushKind",
    "CrushResult",
    "crush_by_type",
    "crush_diff",
    "crush_json",
    "crush_log",
    "crush_search",
    "detect_content_type",
    "estimate_tokens",
    "finalize",
    "no_op",
]
