# ruff: noqa: E402, F403
"""Factory stage-ops helpers — factory failure classification.

Extracted verbatim from the former single-file ``stage_ops`` module during the
lossless decomposition of that god-module.

``factory.py``'s ``_rebind_helper_module`` rebinds these callables into the host
router namespace; the package ``__init__`` rewrites ``__module__`` so the rebind
treats them as package-owned.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("polaris.delivery.http.routers.factory")

from ..mapping import *


def _classify_factory_failure_code(*, stage: str, detail: str) -> str:
    normalized_detail = str(detail or "").lower()
    if "qa_llm_judgement_unavailable" in normalized_detail:
        return "QA_LLM_JUDGEMENT_UNAVAILABLE"
    # Tight provider/auth block signals only. Bare "403" / "forbidden" / "quota"
    # substrings mislabel platform failures (path:line 403, "forbidden path",
    # disk quota). Require provider-shaped HTTP or billing/usage phrasing.
    provider_block_signals = (
        "you've reached your usage limit",
        "usage limit for this billing cycle",
        "usage limit",
        "billing cycle",
        "insufficient_quota",
        "permission_error",
        "message='forbidden'",
        'message="forbidden"',
        "clientresponseerror: 403",
        "clientresponseerror: 429",
        'status_code": 403',
        'status_code": 429',
        "status=403",
        "status=429",
        "http 403",
        "http 429",
        " 403, message=",
        " 429, message=",
        "rate limit exceeded",
        "rate_limit_exceeded",
        "error code: 429",
        "error_code=429",
    )
    if any(signal in normalized_detail for signal in provider_block_signals):
        return "PROVIDER_QUOTA_OR_AUTH_BLOCKED"
    if str(stage or "").strip():
        return "FACTORY_STAGE_FAILED"
    return "FACTORY_RUN_EXCEPTION"


def _factory_failure_suggestion(code: str) -> str:
    if code == "QA_LLM_JUDGEMENT_UNAVAILABLE":
        return "Fix QA LLM connectivity or explicitly disable qa_require_llm_judgement for non-audited dry runs."
    if code == "PROVIDER_QUOTA_OR_AUTH_BLOCKED":
        return (
            "Provider rejected the call (quota exhausted, auth forbidden, or rate limited). "
            "Switch provider/model or refill quota, then restart the Factory run."
        )
    return ""


__all__ = [
    "_classify_factory_failure_code",
    "_factory_failure_suggestion",
]
