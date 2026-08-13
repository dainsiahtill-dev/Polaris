"""Internal implementation module for the provider_helpers package (lossless split).

Owns: provider-agnostic core helpers — ``build_chat_messages_payload`` and
``shrink_max_tokens_for_context_overflow``.

Static F821/F401 are expected and lossless; do not strip.
"""

from __future__ import annotations

import logging
import re
from typing import Any

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

    normalized: list[dict[str, str]] = []
    seen_non_system = False
    for item in chat_messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "")
        if not content.strip():
            continue
        if role == "tool":
            role, content = "user", f"【工具结果】\n{content}"
        elif role == "system":
            if seen_non_system:
                role, content = "user", f"【系统提示】\n{content}"
        elif role not in ("user", "assistant"):
            role = "user"
        if role != "system":
            seen_non_system = True
        if normalized and normalized[-1]["role"] == role:
            normalized[-1]["content"] = f"{normalized[-1]['content']}\n\n{content}"
        else:
            normalized.append({"role": role, "content": content})

    if not normalized:
        normalized = [{"role": "user", "content": prompt}]
    if not any(m["role"] == "user" for m in normalized):
        # Strict chat templates (vLLM qwen3) REJECT conversations without a
        # user turn — observed live (factory-bench 2026-06-12) as intermittent
        # 400 "No user query found in messages" killing whole planning runs:
        # an all-system chat_messages array (user content empty → stripped)
        # passed through untouched. This builder is the SSOT for
        # template-acceptable messages, so the guarantee lives here; the
        # warning keeps a trail to whichever upstream produced the userless
        # array.
        logger.warning(
            "chat_messages contained no user turn (roles=%s); appending user turn",
            [m["role"] for m in normalized],
        )
        prompt_text = str(prompt or "").strip()
        combined_len = sum(len(m["content"]) for m in normalized)
        # W1.5c-5: in the roles-kernel path prompt and chat_messages derive
        # from the SAME messages — appending the full prompt would nearly
        # double the payload (and the duplicate is never budget-accounted).
        # When the array already carries (most of) the prompt content, a short
        # continuation turn satisfies strict templates without the bloat.
        if prompt_text and len(prompt_text) <= combined_len * 0.9:
            normalized.append({"role": "user", "content": "(continue)"})
        else:
            normalized.append({"role": "user", "content": prompt_text or "(continue)"})
    return normalized


__all__ = [
    "_CONTEXT_OVERFLOW_RE",
    "build_chat_messages_payload",
    "shrink_max_tokens_for_context_overflow",
]
