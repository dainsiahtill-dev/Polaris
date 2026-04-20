"""Token estimation with detailed metadata.

This module provides detailed dict return structure with calibration support.

.. deprecated::
    This module is a backward-compatibility shim.
    The canonical location is now ``polaris.kernelone.llm.engine.token_estimator``.

    For simple token counting, use ``polaris.kernelone.llm.engine.token_estimator``.
    For budget management, use ``polaris.domain.services.token_service``.
"""

from __future__ import annotations

import logging as _logging
import math
from typing import Any

# Re-export from authoritative source
from polaris.kernelone.llm.engine.token_estimator import (
    TokenEstimator as _AuthoritativeEstimator,
)

_logger = _logging.getLogger(__name__)

TokenEstimate = dict[str, Any]
_TOKEN_BACKENDS = {"auto", "tiktoken", "heuristic"}


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(parsed) or parsed <= 0.0:
        return float(default)
    return float(parsed)


def _normalize_backend(value: Any) -> str:
    token = str(value or "auto").strip().lower()
    return token if token in _TOKEN_BACKENDS else "auto"


def estimate_tokens_for_text(
    text: str,
    *,
    backend: Any = "auto",
    model: Any = "",
    encoding: Any = "cl100k_base",
    calibration: Any = 1.0,
    fallback_chars_per_token: Any = 4.0,
) -> TokenEstimate:
    """Estimate tokens with detailed metadata.

    This function provides a detailed dict return structure with calibration support.
    For simple token counting, use the authoritative TokenEstimator directly.

    Args:
        text: Input text
        backend: Backend to use (auto/tiktoken/heuristic)
        model: Model name for tiktoken
        encoding: Encoding name for tiktoken fallback
        calibration: Calibration multiplier
        fallback_chars_per_token: Chars per token for heuristic fallback

    Returns:
        Dict with estimated_tokens, raw_tokens, backend info, and calibration data
    """
    content = str(text or "")
    backend_value = _normalize_backend(backend)
    model_value = str(model or "").strip()
    encoding_value = str(encoding or "cl100k_base").strip() or "cl100k_base"
    calibration_value = _positive_float(calibration, 1.0)
    fallback_cpt = _positive_float(fallback_chars_per_token, 4.0)

    raw_tokens = 0
    backend_used = "heuristic"
    encoding_used = f"chars/{fallback_cpt:g}"
    fallback_reason = ""

    # Use authoritative estimator when possible
    if backend_value in {"auto", "tiktoken"}:
        try:
            # Map model to tokenizer hint
            tokenizer_hint = None
            if "gpt-4o" in model_value.lower():
                tokenizer_hint = "o200k_base"
            elif "gpt-4" in model_value.lower() or "gpt-3.5" in model_value.lower():
                tokenizer_hint = "cl100k_base"

            if tokenizer_hint:
                raw_tokens = _AuthoritativeEstimator.estimate(content, tokenizer_hint=tokenizer_hint)
                encoding_used = tokenizer_hint
                backend_used = "tiktoken"
            else:
                raw_tokens = _AuthoritativeEstimator.estimate(content)
                backend_used = "heuristic"
        except (RuntimeError, ValueError) as exc:
            fallback_reason = f"estimation_failed:{type(exc).__name__}"
            _logger.debug("token estimation failed for model=%r: %s", model_value, exc)
            raw_tokens = 0

    if raw_tokens <= 0:
        raw_tokens = max(1, math.ceil(len(content) / fallback_cpt))
        backend_used = "heuristic"

    estimated_tokens = max(1, round(raw_tokens * calibration_value))
    chars_per_token = float(len(content)) / float(raw_tokens) if raw_tokens > 0 else fallback_cpt

    return {
        "estimated_tokens": int(estimated_tokens),
        "raw_tokens": int(raw_tokens),
        "backend_requested": backend_value,
        "backend_used": backend_used,
        "encoding_requested": encoding_value,
        "encoding_used": encoding_used,
        "model": model_value,
        "calibration": float(calibration_value),
        "fallback_chars_per_token": float(fallback_cpt),
        "chars_per_token": float(chars_per_token),
        "fallback_reason": fallback_reason,
    }


def estimate_tokens_from_chars(
    chars: int,
    *,
    chars_per_token: Any,
    calibration: Any = 1.0,
) -> TokenEstimate:
    """Estimate tokens from character count with calibration.

    Args:
        chars: Character count
        chars_per_token: Chars per token ratio
        calibration: Calibration multiplier

    Returns:
        Dict with estimated_tokens, raw_tokens, and calibration data
    """
    chars_value = max(0, int(chars))
    cpt = _positive_float(chars_per_token, 4.0)
    calibration_value = _positive_float(calibration, 1.0)

    raw_tokens = max(1, math.ceil(chars_value / cpt)) if chars_value > 0 else 1
    estimated_tokens = max(1, round(raw_tokens * calibration_value))
    return {
        "estimated_tokens": int(estimated_tokens),
        "raw_tokens": int(raw_tokens),
        "chars_per_token": float(cpt),
        "calibration": float(calibration_value),
    }
