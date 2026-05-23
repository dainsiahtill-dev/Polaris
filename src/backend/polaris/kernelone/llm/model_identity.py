"""Model identity helpers for LLM runtime contracts."""

from __future__ import annotations

import re

_SEPARATOR_RE = re.compile(r"[\s_-]+")
_PATH_RE = re.compile(r"[/:]+")


def model_identity_key(model: str) -> str:
    token = str(model or "").strip().casefold()
    token = _PATH_RE.sub("-", token)
    token = _SEPARATOR_RE.sub("-", token)
    return token.strip("-")


def model_identity_aliases(model: str) -> set[str]:
    """Return comparable keys for provider-qualified model identifiers."""

    token = str(model or "").strip()
    if not token:
        return set()

    aliases = {model_identity_key(token)}
    parts = [part for part in _PATH_RE.split(token) if part.strip()]
    if parts:
        aliases.add(model_identity_key(parts[-1]))
    return {alias for alias in aliases if alias}


def model_identity_equal(left: str, right: str) -> bool:
    left_aliases = model_identity_aliases(left)
    right_aliases = model_identity_aliases(right)
    return bool(left_aliases and right_aliases and left_aliases.intersection(right_aliases))


__all__ = ["model_identity_aliases", "model_identity_equal", "model_identity_key"]
