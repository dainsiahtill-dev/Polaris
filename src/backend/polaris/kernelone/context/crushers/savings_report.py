"""Deterministic savings observation for the T2-B content-type crushers.

OBSERVE-FIRST step (no live wiring). This module does **not** touch the hot
compression path; it is an additive, on-demand benchmark that runs
:func:`~polaris.kernelone.context.crushers.crush_by_type` over representative,
synthetic-but-generic samples and reports the token savings per content type.

Its purpose is to *prove* crush token-quality on representative JSON / log /
diff / search payloads so a later wave can justify re-anchoring the crushers to
the real live path. Until that proof exists, the crushers stay off the hot
path.

Design constraints (mirrors the crushers themselves):
    - KernelOne-only: NO Polaris business semantics and NO target-project code.
      Every sample is generic, structurally representative scaffolding.
    - Token estimation MUST go through the canonical estimator (re-exported from
      ``crushers`` as ``estimate_tokens``). This module adds **no** new
      estimation formula.
    - Fail-closed: the per-type ``reject_if_not_smaller_held`` flag records
      whether the crusher honoured the "never expand" invariant; a violation is
      surfaced (not silently passed) via :meth:`SavingsReport.all_invariants_held`.
    - Off the hot path: invocable on demand only (``python -m`` / direct call).

All text I/O is explicit UTF-8.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from polaris.kernelone.context.crushers import (
    CrushKind,
    crush_by_type,
    estimate_tokens,
)

__all__ = [
    "SavingsReport",
    "TypeSavings",
    "build_sample_corpus",
    "main",
    "measure_savings",
    "render_report",
]


@dataclass(frozen=True)
class TypeSavings:
    """Measured crush savings for a single representative sample.

    Attributes:
        label: Stable identifier for the sample (e.g. ``"json_records"``).
        original_tokens: Token estimate of the raw sample.
        crushed_tokens: Token estimate of the crushed result.
        ratio: ``crushed_tokens / original_tokens`` (1.0 when no shrink / empty
            input). Always in ``[0.0, 1.0]``.
        kind: Which crusher produced the result (or ``NONE`` when no crush
            applied / was rejected).
        reject_if_not_smaller_held: True when the crusher honoured the
            "never expand" invariant for this sample (``crushed_tokens <=
            original_tokens``). False signals a real invariant violation.
    """

    label: str
    original_tokens: int
    crushed_tokens: int
    ratio: float
    kind: CrushKind
    reject_if_not_smaller_held: bool

    @property
    def saved_tokens(self) -> int:
        """Absolute token reduction for this sample (>= 0 when invariant held)."""
        return self.original_tokens - self.crushed_tokens

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-compatible dict for reporting.

        Returns:
            A mapping with the per-type savings fields. ``kind`` is rendered as
            its string value for stable, tool-friendly output.
        """
        return {
            "label": self.label,
            "kind": self.kind.value,
            "original_tokens": self.original_tokens,
            "crushed_tokens": self.crushed_tokens,
            "saved_tokens": self.saved_tokens,
            "ratio": round(self.ratio, 4),
            "reject_if_not_smaller_held": self.reject_if_not_smaller_held,
        }


@dataclass(frozen=True)
class SavingsReport:
    """Aggregate crush-savings observation across every representative sample.

    Attributes:
        entries: Per-sample savings, in corpus order.
    """

    entries: Sequence[TypeSavings] = field(default_factory=tuple)

    @property
    def total_original_tokens(self) -> int:
        """Sum of original token estimates across all samples."""
        return sum(e.original_tokens for e in self.entries)

    @property
    def total_crushed_tokens(self) -> int:
        """Sum of crushed token estimates across all samples."""
        return sum(e.crushed_tokens for e in self.entries)

    @property
    def total_ratio(self) -> float:
        """Aggregate ``crushed/original`` ratio (1.0 when corpus is empty)."""
        original = self.total_original_tokens
        if original <= 0:
            return 1.0
        return self.total_crushed_tokens / original

    def all_invariants_held(self) -> bool:
        """Return True only when every sample honoured the never-expand rule.

        Fail-closed surface: a False here means at least one crusher returned a
        result whose token estimate exceeded the original. Callers (tests, the
        CLI) must treat False as a failure, never silently pass it.

        Returns:
            True when every entry's ``reject_if_not_smaller_held`` is True.
        """
        return all(e.reject_if_not_smaller_held for e in self.entries)

    def to_dict(self) -> dict[str, object]:
        """Serialize the full report to a JSON-compatible dict.

        Returns:
            A mapping with per-sample entries plus aggregate totals.
        """
        return {
            "entries": [e.to_dict() for e in self.entries],
            "total_original_tokens": self.total_original_tokens,
            "total_crushed_tokens": self.total_crushed_tokens,
            "total_saved_tokens": self.total_original_tokens - self.total_crushed_tokens,
            "total_ratio": round(self.total_ratio, 4),
            "all_invariants_held": self.all_invariants_held(),
        }


def _json_records_sample(count: int = 300) -> str:
    """Build a generic JSON array of homogeneous records.

    Generic structural scaffolding only -- field names are abstract
    (``id`` / ``label`` / ``score`` / ``active``) and carry no business meaning.

    Args:
        count: Number of records to emit.

    Returns:
        A JSON string large enough to exceed the min-crush byte threshold.
    """
    rows = [{"id": i, "label": f"record-{i}", "score": i % 97, "active": bool(i % 2)} for i in range(count)]
    return json.dumps(rows)


def _json_numbers_sample(count: int = 500) -> str:
    """Build a generic JSON array of integers (numeric-outlier crush path).

    Args:
        count: Number of integers to emit.

    Returns:
        A JSON string of a plain integer array.
    """
    return json.dumps(list(range(count)))


def _log_sample(count: int = 120) -> str:
    """Build a generic, repetitive timestamped log block.

    Lines repeat structurally (collapse path) with one distinct ERROR line so
    the sample exercises both collapse and error-preservation. No real hostnames
    or project identifiers -- purely abstract tokens.

    Args:
        count: Number of log lines to emit.

    Returns:
        A multi-line log string.
    """
    lines = [f"2026-01-01 00:00:0{i % 10} INFO worker {i % 3} processed unit 42" for i in range(count)]
    lines.insert(count // 2, "2026-01-01 00:00:09 ERROR generic failure on resource X")
    return "\n".join(lines)


def _diff_sample(context_lines: int = 60) -> str:
    """Build a generic unified-diff block dominated by context lines.

    Long context runs (collapse path) bracket a small set of changes. File names
    are abstract placeholders, not real project paths.

    Args:
        context_lines: Number of unchanged context lines.

    Returns:
        A unified-diff string.
    """
    lines = ["diff --git a/file b/file", "index 0000000..1111111 100644", "@@ -1,80 +1,80 @@"]
    lines += [f" context line {i}" for i in range(context_lines)]
    lines += [f"-removed line {i}" for i in range(4)]
    lines += [f"+added line {i}" for i in range(4)]
    return "\n".join(lines)


def _search_sample(repeats: int = 60) -> str:
    """Build a generic ripgrep-style search block with duplicate hits.

    Duplicate ``path:line:`` rows exercise the dedup path. Paths are abstract
    placeholders, not real project files.

    Args:
        repeats: Number of duplicate rows to emit.

    Returns:
        A multi-line search-result string.
    """
    dup = ["src/module.ext:10: matched token here" for _ in range(repeats)]
    distinct = [f"src/module.ext:{20 + i}: distinct match {i}" for i in range(8)]
    return "\n".join(dup + distinct)


def build_sample_corpus() -> Mapping[str, str]:
    """Build the representative synthetic-but-generic sample corpus.

    Each value is structurally representative of a real tool output (JSON, logs,
    diffs, search results) while containing NO business or target-project
    content (S8). Every sample is sized to exceed
    :data:`~polaris.kernelone.context.crushers.MIN_CRUSH_BYTES` so the crushers
    actually engage.

    Returns:
        An ordered mapping ``label -> raw_sample_text``.
    """
    return {
        "json_records": _json_records_sample(),
        "json_numbers": _json_numbers_sample(),
        "log_repetitive": _log_sample(),
        "diff_context_heavy": _diff_sample(),
        "search_duplicates": _search_sample(),
    }


def _measure_one(label: str, text: str) -> TypeSavings:
    """Crush one sample and capture its savings + invariant status.

    Token figures come from :func:`crush_by_type` (canonical estimator), with
    the original recomputed via the same estimator for a defensive,
    independent cross-check of the never-expand invariant.

    Args:
        label: Stable identifier for the sample.
        text: Raw sample text.

    Returns:
        The measured :class:`TypeSavings`.
    """
    result = crush_by_type(text)
    # Independent cross-check of the original size via the canonical estimator;
    # confirms the crusher did not understate the baseline it compares against.
    estimated_original = estimate_tokens(text)
    invariant_held = result.crushed_tokens <= result.original_tokens and result.original_tokens == estimated_original
    return TypeSavings(
        label=label,
        original_tokens=result.original_tokens,
        crushed_tokens=result.crushed_tokens,
        ratio=result.ratio,
        kind=result.kind,
        reject_if_not_smaller_held=invariant_held,
    )


def measure_savings(corpus: Mapping[str, str] | None = None) -> SavingsReport:
    """Run :func:`crush_by_type` over the corpus and report per-type savings.

    Deterministic and side-effect free: no I/O, no live-path wiring, no LLM.

    Args:
        corpus: Optional ``label -> text`` mapping. Defaults to
            :func:`build_sample_corpus`.

    Returns:
        A :class:`SavingsReport` aggregating every sample's savings.
    """
    samples = build_sample_corpus() if corpus is None else corpus
    entries = tuple(_measure_one(label, text) for label, text in samples.items())
    return SavingsReport(entries=entries)


def render_report(report: SavingsReport) -> str:
    """Render a report as a deterministic, UTF-8 JSON string.

    Args:
        report: The report to render.

    Returns:
        A pretty-printed JSON string (UTF-8 safe, non-ASCII preserved).
    """
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


def main() -> int:
    """On-demand entry point: measure savings and print the JSON report.

    Returns:
        Process exit code: ``0`` when every never-expand invariant held,
        ``1`` otherwise (fail-closed).
    """
    report = measure_savings()
    print(render_report(report))
    return 0 if report.all_invariants_held() else 1


if __name__ == "__main__":  # pragma: no cover - CLI shim
    raise SystemExit(main())
