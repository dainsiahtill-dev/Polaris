"""Cache-stability observers for the local-backend prompt cache (Headroom T1-B).

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

防重复造轮子提示
================
This package is the SINGLE implementation of per-session prefix-stability
OBSERVATION. It computes a deterministic SHA-256 fingerprint of the cache-hot
prefix and reports drift + volatile tokens that would bust the local vLLM/
llama.cpp prompt cache.

- It is OBSERVATION ONLY: it does NOT mutate request bytes and does NOT reorder
  tools. Prefix normalization (tool sorting, JSON-schema key ordering, moving
  volatile tokens out of the prefix) is a LATER step — do NOT add it here.
- Do NOT create a second drift/fingerprint module elsewhere; extend this one.
- §8: keep this a generic platform capability — no project names, file
  templates, domain models, or hardcoded paths.

See docs/blueprints/HEADROOM_PREFIX_DRIFT_OBSERVER_20260616.md.
"""

from __future__ import annotations

from .drift_detector import (
    PrefixDriftObserver,
    PrefixDriftReport,
    PrefixSlice,
    VolatileFinding,
    VolatileKind,
    extract_prefix,
    fingerprint_prefix,
    get_prefix_drift_observer,
    scan_volatile_tokens,
)

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
