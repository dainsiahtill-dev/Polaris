"""Reversible PII masking and restoration."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Final

_EMAIL_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

_JWT_HEADER_RE: Final[re.Pattern[str]] = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9._-]+$")

_API_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r"^(sk|api|key|token|secret|password|passwd|pwd)[_-]?[A-Za-z0-9]{16,64}$",
    re.IGNORECASE,
)

_PHONE_RE: Final[re.Pattern[str]] = re.compile(r"^\+?[1-9]\d{1,14}$")

_CC_RE: Final[re.Pattern[str]] = re.compile(r"^\d{13,19}$")

_PII_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b sk-[A-Za-z0-9]{32,} \b"),  # OpenAI style
    re.compile(r"\b [A-Za-z0-9]{20,} (?:api)?key [A-Za-z0-9_-]* \b", re.IGNORECASE),  # Generic API key
)


@dataclass(frozen=True)
class MaskedPayload:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)


def _is_valid_base64(s: str) -> bool:
    try:
        if len(s) % 4 != 0:
            s = s + "=" * (4 - len(s) % 4)
        base64.urlsafe_b64decode(s)
        return True
    except (ValueError, TypeError):
        return False


def _is_valid_jwt(token: str) -> bool:
    parts = token.split(".")
    if len(parts) != 3:
        return False
    return all(_is_valid_base64(part) for part in parts)


def _is_valid_email(s: str) -> bool:
    return bool(_EMAIL_RE.match(s)) and "@" in s and "." in s.split("@")[1]


def _is_valid_phone(s: str) -> bool:
    digits = re.sub(r"[^\d]", "", s)
    return 10 <= len(digits) <= 15


def _is_valid_credit_card(s: str) -> bool:
    digits = re.sub(r"[^\d]", "", s)
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    is_even = False
    for digit in reversed(digits):
        n = ord(digit) - 48
        if is_even:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
        is_even = not is_even
    return checksum % 10 == 0


def _is_api_key(s: str) -> bool:
    return bool(_API_KEY_RE.match(s.strip()))


class PIIReversibleMasker:
    """Mask PII tokens and support exact restoration."""

    def mask(self, text: str) -> MaskedPayload:
        mapping: dict[str, str] = {}
        masked = str(text)
        next_id = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal next_id
            original = match.group(0).strip()
            for token, value in mapping.items():
                if value == original:
                    return token

            validated = False
            if original.startswith("eyJ") and "." in original:
                validated = _is_valid_jwt(original)
            elif "@" in original and "." in original:
                validated = _is_valid_email(original)
            elif re.match(r"^\+?\d+$", original):
                if _is_valid_phone(original) or _is_valid_credit_card(original):
                    validated = True
            elif original.lower().startswith(("sk-", "api-", "key-", "token-", "secret-")) or _is_api_key(original):
                validated = True

            if not validated:
                return match.group(0)

            token = f"\x00PII_{next_id:04d}\x00"
            mapping[token] = original
            next_id += 1
            return token

        for pattern in _PII_PATTERNS:
            masked = pattern.sub(replace, masked)
        return MaskedPayload(text=masked, mapping=mapping)

    def restore(self, masked_text: str, mapping: dict[str, str]) -> str:
        restored = str(masked_text)
        for token in sorted(mapping.keys(), key=len, reverse=True):
            restored = restored.replace(token, mapping[token])
        return restored


__all__ = [
    "MaskedPayload",
    "PIIReversibleMasker",
]
