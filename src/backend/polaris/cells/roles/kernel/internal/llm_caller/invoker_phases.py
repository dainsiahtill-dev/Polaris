"""Phase helpers extracted from :class:`LLMInvoker` (G8c decomposition).

Behavior-preserving extraction of the repeated, side-effect-free idioms from
``LLMInvoker.call`` / ``LLMInvoker.call_structured``. These helpers contain NO
class state and are pure functions; the stateful orchestration stays on the
``LLMInvoker`` methods themselves.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile

    from .response_types import PreparedLLMRequest


@dataclass
class FallbackLadderResult:
    """Outcome of the call-phase fallback ladder.

    Carries the mutated locals (response/active_request/profile/prepared/model/
    fallback flags/status) back to the orchestrator so the downstream error and
    success finalizers read a single returned bundle instead of closure
    mutation.
    """

    response: Any
    active_request: Any
    profile: RoleProfile
    prepared: PreparedLLMRequest
    model: str
    native_tool_fallback: bool
    native_response_fallback: bool
    is_response_ok: bool
    response_error: str


def read_response_status(response: Any) -> tuple[bool, str]:
    """Return ``(is_response_ok, response_error)`` for an executor response.

    Reproduces the exact idiom duplicated across the call/call_structured
    fallback rungs:

    * ``response_ok`` defaults to ``True`` when the attribute is missing, and
      is only honored as a boolean when it actually IS a ``bool``.
    * The error string is read via ``hasattr`` first (so an explicitly-set
      ``None`` is distinguished from a truly-missing attribute), stripped, and
      coerced to ``""`` when ``None``.
    * The turn is treated as FAILED if ``ok`` is ``False`` OR a non-empty error
      string is present (handles providers that return ``ok=True`` together
      with an error like ``"Unknown field: tools"``).
    """
    response_ok = getattr(response, "ok", True)
    # Detect error: hasattr checks instance __dict__ first (True for explicitly set None).
    # getattr with default only triggers for truly missing attributes.
    _has_error = hasattr(response, "error")
    _raw_error = getattr(response, "error", None) if _has_error else None
    response_error = str(_raw_error or "").strip() if _raw_error is not None else ""
    # Treat as failed if ok=False or if error string is present (handles providers
    # that return ok=True with an error field like "Unknown field: tools")
    is_response_ok = (bool(response_ok) if isinstance(response_ok, bool) else True) and not response_error
    return is_response_ok, response_error


__all__ = ["FallbackLadderResult", "read_response_status"]
