"""Context gateway constants - Regex patterns, CJK ranges, and mapping tables.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import re

# High-priority dialog acts that get boosted priority in message selection
HIGH_PRIORITY_DIALOG_ACTS: frozenset[str] = frozenset({"affirm", "deny", "pause", "redirect", "clarify"})

# Route priority mapping: PATCH > SUMMARIZE > ARCHIVE > CLEAR
ROUTE_PRIORITY: dict[str, int] = {"patch": 3, "summarize": 2, "archive": 1, "clear": 0}

# Unicode 混淆字符映射（用于检测提示词注入）
# 注意：只包含真正用于视觉混淆的Unicode字符，不包含ASCII数字
_UNICODE_CONFUSION_MAP = {
    # Cyrillic look-alikes (Unicode block: U+0400-U+04FF)
    "а": "a",  # U+0430 CYRILLIC SMALL LETTER A
    "е": "e",  # U+0435 CYRILLIC SMALL LETTER IE
    "о": "o",  # U+043E CYRILLIC SMALL LETTER O
    "р": "p",  # U+0440 CYRILLIC SMALL LETTER ER
    "с": "c",  # U+0441 CYRILLIC SMALL LETTER ES
    "х": "x",  # U+0445 CYRILLIC SMALL LETTER HA
    "і": "i",  # U+0456 CYRILLIC SMALL LETTER BYELORUSSIAN-UKRAINIAN I
    "ӏ": "l",  # U+04CF CYRILLIC SMALL LETTER PALOCHKA
    # Greek look-alikes (Unicode block: U+0370-U+03FF)
    "ɡ": "g",  # U+0261 LATIN SMALL LETTER SCRIPT G (phonetic)
    "ν": "v",  # U+03BD GREEK SMALL LETTER NU
    "ω": "w",  # U+03C9 GREEK SMALL LETTER OMEGA
    "ɑ": "a",  # U+0251 LATIN SMALL LETTER ALPHA
    "ο": "o",  # U+03BF GREEK SMALL LETTER OMICRON
    # Other confusables
    "｜": "|",  # U+FF5C FULLWIDTH VERTICAL LINE
}

# Base64 编码模式 - 更精确的检测，避免误报UUID和哈希
_BASE64_EXPLICIT_PATTERN = re.compile(r"(?:base64:|BASE64:)[A-Za-z0-9+/]{20,}={0,2}", re.IGNORECASE)

_BASE64_CONTENT_PATTERN = re.compile(
    r"[A-Za-z0-9+/]{40,}={0,2}",
    re.IGNORECASE,
)

_PROMPT_INJECTION_PATTERNS = (
    re.compile(
        r"\b(ignore|bypass|forget|disregard|override)\b.{0,30}\b(previous|prior|system|instruction|rule|limit)s?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\byou\s+are\b", re.IGNORECASE),
    re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE),
    re.compile(r"<\s*/?\s*thinking\s*>", re.IGNORECASE),
    re.compile(r"<\s*/?\s*tool_call\s*>", re.IGNORECASE),
    re.compile(r"don't\s+think\b", re.IGNORECASE),
    re.compile(r"ignore\s+all\s+previous", re.IGNORECASE),
    re.compile(r"new\s+instruction", re.IGNORECASE),
    re.compile(r"角色设定|提示词|系统提示|忽略.*之前|无视.*规则|忘记.*指令", re.IGNORECASE),
    re.compile(r"你是.*而不是|从现在起.*是|现在你是", re.IGNORECASE),
    re.compile(r"<\|.*\|>", re.IGNORECASE),
    re.compile(r"\[INST\]|\[/INST\]", re.IGNORECASE),
    re.compile(r"dan\s+mode", re.IGNORECASE),
    re.compile(r"developer\s+mode", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
)

MAX_USER_MESSAGE_CHARS = 4000

# CJK Unicode 范围定义（用于高效字符检测）
_CJK_RANGES = (
    (0x3000, 0x303F),  # CJK Symbols and Punctuation
    (0x3040, 0x309F),  # Hiragana (平假名)
    (0x30A0, 0x30FF),  # Katakana (片假名)
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs (常用汉字)
    (0xAC00, 0xD7AF),  # Hangul Syllables (韩文)
    (0xFF00, 0xFFEF),  # Fullwidth ASCII variants (全角字符)
    (0x20000, 0x2EBEF),  # CJK Unified Ideographs Extension B-F
)


def normalize_confusable(text: str) -> str:
    """将可混淆字符标准化为 ASCII"""
    result = []
    for char in text:
        result.append(_UNICODE_CONFUSION_MAP.get(char, char))
    return "".join(result)


def is_likely_base64_payload(text: str) -> bool:
    """判断文本是否可能是 Base64 编码的 payload。"""
    if _BASE64_EXPLICIT_PATTERN.search(text):
        return True

    for match in _BASE64_CONTENT_PATTERN.finditer(text):
        content = match.group()

        if re.match(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
            content,
        ):
            continue

        if not re.search(r"[+/]", content):
            continue

        if re.match(r"^[0-9a-fA-F]{40}$", content):
            continue

        if "+" in content or "/" in content:
            return True

    return False


def is_cjk_char(char: str) -> bool:
    """检查字符是否属于CJK (中日韩) 字符集"""
    code = ord(char)
    if code < 0x3000:
        return False

    for start, end in _CJK_RANGES:
        if code < start:
            return False
        if start <= code <= end:
            return True
    return False


__all__ = [
    "HIGH_PRIORITY_DIALOG_ACTS",
    "MAX_USER_MESSAGE_CHARS",
    "ROUTE_PRIORITY",
    "is_cjk_char",
    "is_likely_base64_payload",
    "normalize_confusable",
]
