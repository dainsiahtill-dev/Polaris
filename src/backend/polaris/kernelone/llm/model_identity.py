"""Model identity helpers for LLM runtime contracts."""

from __future__ import annotations


def model_identity_key(model: str) -> str:
    return str(model or "").strip().casefold()


def model_identity_equal(left: str, right: str) -> bool:
    return model_identity_key(left) == model_identity_key(right)


__all__ = ["model_identity_equal", "model_identity_key"]
