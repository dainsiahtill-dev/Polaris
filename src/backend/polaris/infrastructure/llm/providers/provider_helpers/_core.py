"""Internal implementation module for the provider_helpers package (lossless split).

Owns: provider-agnostic core helpers — ``build_chat_messages_payload`` and
``shrink_max_tokens_for_context_overflow``.

Static F821/F401 are expected and lossless; do not strip.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from polaris.kernelone.llm.engine.provider_native_request import build_openai_native_messages

logger = logging.getLogger(__name__)


_CONTEXT_OVERFLOW_RE = re.compile(
    r"maximum context length is (\d+) tokens.*?(\d+) output tokens.*?at least (\d+) input tokens",
    re.DOTALL,
)


def shrink_max_tokens_for_context_overflow(payload: dict[str, Any], error_body: str) -> bool:
    """Self-heal a server-side context-overflow 400 using the SERVER's numbers.

    vLLM rejects requests where prompt + max_tokens exceeds max_model_len and
    reports the exact window/input/output counts. Client-side token estimation
    can never match the server tokenizer exactly (live: a planning payload
    estimated under budget was counted as 8193 by the server, three retries of
    the identical request all failed and killed the run). When the error body
    carries the numbers, recompute max_tokens from the server truth and let
    the caller retry once. Returns True when payload was adjusted.
    """
    match = _CONTEXT_OVERFLOW_RE.search(error_body or "")
    if not match:
        return False
    window, requested_output, reported_input = (int(g) for g in match.groups())
    new_max = window - reported_input - 16
    try:
        current = int(payload.get("max_tokens") or 0)
    except (TypeError, ValueError):
        current = 0
    if new_max < 64 or (current and new_max >= current):
        return False
    payload["max_tokens"] = new_max
    logger.warning(
        "[provider-helpers] context overflow self-heal: window=%s input=%s requested_output=%s -> max_tokens=%s",
        window,
        reported_input,
        requested_output,
        new_max,
    )
    return True


def build_chat_messages_payload(
    chat_messages: Any,
    prompt: str,
    system_prompt: str | None = None,
) -> list[dict[str, str]]:
    """Build a chat-completions ``messages`` array, preserving real role structure.

    ADR-0090 W1.5: weak local models depend heavily on their chat template's
    role anchoring. When the caller supplies a structured ``chat_messages``
    array, use it (system/user/assistant pass through; tool results become
    user turns with a marker; consecutive same-role turns merge; supplemental
    mid-conversation system turns are downgraded to marked user turns because
    strict templates such as vLLM's reject non-leading system messages).
    Otherwise fall back to the legacy single-user-message flattening.

    Shared by openai_compat AND ollama providers — keep provider-agnostic.
    """
    if not isinstance(chat_messages, list) or not chat_messages:
        fallback: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        if system_prompt:
            fallback.insert(0, {"role": "system", "content": str(system_prompt)})
        return fallback

    populated = [item for item in chat_messages if isinstance(item, dict) and str(item.get("content") or "").strip()]
    if not populated:
        return [{"role": "user", "content": prompt}]
    if all(str(item.get("role") or "").strip().lower() in {"system", "assistant"} for item in populated):
        logger.warning(
            "chat_messages contained no user turn (roles=%s); appending user turn",
            [item.get("role") for item in populated],
        )
    # Same pure KernelOne owner as Factory's native request authority. Keep
    # legacy fallback admission here, never a second role/merge algorithm.
    return build_openai_native_messages(populated, fallback_prompt=prompt)


__all__ = [
    "_CONTEXT_OVERFLOW_RE",
    "build_chat_messages_payload",
    "shrink_max_tokens_for_context_overflow",
]
