"""Per-session prefix-stability observer (Headroom T1-B step 1).

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

The local Director loop runs against vLLM (automatic prefix caching) and
llama.cpp (prompt cache). A prefix hit lowers TTFT and raises throughput, so a
stable cache-hot prefix (role ``system_prompt`` + the leading/frozen ``system``
segment the gateway assembles) is a real throughput lever. When that prefix
changes byte-for-byte across consecutive assemblies for the same session, the
local backend's prompt cache is busted and the whole prefix is recomputed.

This module is a **pure, deterministic, fail-safe DIAGNOSTIC**: given the
assembled prefix it computes a per-session SHA-256 fingerprint, detects drift
across assemblies for the same session, and flags VOLATILE tokens
(ISO-8601 timestamps, UUIDv4, run_id-like fields) that would bust the cache.

It is OBSERVATION ONLY — it never mutates request bytes, never reorders tools,
and never raises into a turn. Normalization (tool sorting / moving volatile
tokens out of the prefix) is explicitly out of scope here and belongs to a
later step (see docs/blueprints/HEADROOM_PREFIX_DRIFT_OBSERVER_20260616.md).

§8 compliance: pure platform capability (regex / hash / counting). No project
names, file templates, domain models, or hardcoded paths.
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "PrefixDriftObserver",
    "PrefixDriftReport",
    "PrefixSlice",
    "VolatileFinding",
    "VolatileKind",
    "extract_prefix",
    "fingerprint_prefix",
    "get_prefix_drift_observer",
    "scan_volatile_tokens",
]

# Maximum characters of a volatile-token sample we keep in a finding. Keeping a
# short sample makes the report actionable without copying large prefix spans.
_VOLATILE_SAMPLE_MAX_CHARS = 64


class VolatileKind(str, Enum):
    """Categories of cache-busting volatile tokens we report (warnings only)."""

    ISO8601_TIMESTAMP = "iso8601_timestamp"
    UUIDV4 = "uuidv4"
    RUN_ID_LIKE = "run_id_like"


# Conservative, precompiled patterns. We prefer false-negatives over
# false-positives: a warning that is wrong is noise, so each pattern is strict.
_UUIDV4_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b")
# ISO-8601 with a 'T' separator (date+time); optional fractional seconds and a
# timezone designator (Z or ±HH:MM). The 'T' separator keeps it from matching a
# bare date that may legitimately be static.
_ISO8601_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b")
# run_id-like: a slug ending in a long numeric suffix (e.g. ``pm-00001``,
# ``run-20260616-001``) or a hex run token. Excludes the UUIDs already covered.
_RUN_ID_LIKE_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*[-_]\d{4,}\b")


@dataclass(frozen=True, slots=True)
class VolatileFinding:
    """A volatile token category detected in the prefix, with an occurrence count."""

    kind: VolatileKind
    sample: str
    count: int


@dataclass(frozen=True, slots=True)
class PrefixSlice:
    """The cache-hot prefix that gets fingerprinted.

    ``text`` is the concatenation (in order) of the role ``system_prompt`` and
    the leading contiguous ``system`` messages the gateway assembled — i.e. the
    span a local prompt cache would treat as the stable prefix. ``message_count``
    counts the gateway messages contributing to the slice (excluding the
    separately-provided ``system_prompt``); ``segment_roles`` records their roles
    for diagnostics.
    """

    text: str
    message_count: int
    segment_roles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PrefixDriftReport:
    """Result of observing one prefix assembly for a session."""

    fingerprint: str
    drifted: bool
    first_seen: bool
    previous_fingerprint: str
    volatile_findings: tuple[VolatileFinding, ...] = field(default_factory=tuple)
    prefix_chars: int = 0
    prefix_message_count: int = 0


def _coerce_content(value: object) -> str:
    """Best-effort, exception-free coercion of a message ``content`` to text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def extract_prefix(
    messages: Sequence[Mapping[str, object]] | None,
    system_prompt: str | None = None,
) -> PrefixSlice:
    """Slice the cache-hot prefix out of the assembled messages.

    The prefix is ``system_prompt`` (if provided) followed by the leading
    contiguous run of ``system`` messages, stopping at the first non-system
    message. This mirrors how a local prompt cache treats the stable prefix.

    Pure: never mutates ``messages``, never deserializes content, never raises.
    """
    parts: list[str] = []
    roles: list[str] = []
    if system_prompt:
        parts.append(str(system_prompt))

    counted = 0
    if messages:
        for message in messages:
            if not isinstance(message, Mapping):
                # Malformed entry ends the contiguous system prefix.
                break
            role = str(message.get("role") or "").strip().lower()
            if role != "system":
                break
            parts.append(_coerce_content(message.get("content")))
            roles.append(role)
            counted += 1

    return PrefixSlice(
        text="\n".join(parts),
        message_count=counted,
        segment_roles=tuple(roles),
    )


def fingerprint_prefix(prefix: PrefixSlice) -> str:
    """Deterministic SHA-256 hex fingerprint of the prefix text.

    An empty prefix yields ``""`` (no fingerprint) rather than the hash of the
    empty string, so callers can distinguish "no cache-hot prefix" from drift.
    """
    text = prefix.text
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _truncate_sample(token: str) -> str:
    sample = token.strip()
    if len(sample) > _VOLATILE_SAMPLE_MAX_CHARS:
        return sample[:_VOLATILE_SAMPLE_MAX_CHARS]
    return sample


def scan_volatile_tokens(text: str) -> tuple[VolatileFinding, ...]:
    """Flag cache-busting volatile tokens in the prefix text (warnings only).

    Order of detection matters: UUIDv4 and ISO-8601 are matched first and their
    spans are blanked out before the looser run_id-like pattern runs, so a UUID
    is never double-reported as a run_id. Pure and exception-free.
    """
    if not text:
        return ()

    findings: list[VolatileFinding] = []
    remaining = text

    for kind, pattern in (
        (VolatileKind.UUIDV4, _UUIDV4_RE),
        (VolatileKind.ISO8601_TIMESTAMP, _ISO8601_RE),
        (VolatileKind.RUN_ID_LIKE, _RUN_ID_LIKE_RE),
    ):
        matches = pattern.findall(remaining)
        if matches:
            findings.append(
                VolatileFinding(
                    kind=kind,
                    sample=_truncate_sample(str(matches[0])),
                    count=len(matches),
                )
            )
            # Blank out matched spans so a later, looser pattern cannot
            # re-report the same token under a different kind.
            remaining = pattern.sub(" ", remaining)

    return tuple(findings)


class PrefixDriftObserver:
    """Cross-assembly prefix-drift detector keyed by session.

    Holds a ``session_key -> last_fingerprint`` map so consecutive assemblies for
    the same session can be compared. Thread-safe. ``observe`` never raises; on
    any internal error it returns a safe, drift-free report.
    """

    def __init__(self) -> None:
        self._last_fingerprint: dict[str, str] = {}
        self._lock = threading.Lock()

    def observe(self, session_key: str, prefix: PrefixSlice) -> PrefixDriftReport:
        """Compute the fingerprint, compare to the session's last, and report drift."""
        try:
            fingerprint = fingerprint_prefix(prefix)
            volatile = scan_volatile_tokens(prefix.text)
            key = str(session_key or "")
            with self._lock:
                previous = self._last_fingerprint.get(key, "")
                self._last_fingerprint[key] = fingerprint
            first_seen = not previous
            # Drift requires a non-empty fingerprint on both sides; an empty
            # prefix is reported as not-drifted rather than as a spurious change.
            drifted = bool(previous) and bool(fingerprint) and previous != fingerprint
            return PrefixDriftReport(
                fingerprint=fingerprint,
                drifted=drifted,
                first_seen=first_seen,
                previous_fingerprint=previous,
                volatile_findings=volatile,
                prefix_chars=len(prefix.text),
                prefix_message_count=int(prefix.message_count),
            )
        except Exception:  # noqa: BLE001 - diagnostics must never break a turn
            return PrefixDriftReport(
                fingerprint="",
                drifted=False,
                first_seen=True,
                previous_fingerprint="",
                volatile_findings=(),
                prefix_chars=0,
                prefix_message_count=0,
            )

    def reset(self) -> None:
        """Clear all session state (test/maintenance helper)."""
        with self._lock:
            self._last_fingerprint.clear()


_OBSERVER_SINGLETON = PrefixDriftObserver()


def get_prefix_drift_observer() -> PrefixDriftObserver:
    """Return the process-wide prefix-drift observer.

    Module-level state lets drift be detected across the per-turn gateway
    instances (each turn builds a fresh RoleContextGateway), mirroring the
    module-level learning-key pattern used by ProjectionEngine.
    """
    return _OBSERVER_SINGLETON
