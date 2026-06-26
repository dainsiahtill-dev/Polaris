"""Shared PM directive language-contract predicates.

These helpers keep deterministic synthesis and quality normalization on the
same interpretation of a directive.  A language-specific hard gate must only
activate for the language that the user actually requested; generic examples or
cross-language acceptance boilerplate are not enough.
"""

from __future__ import annotations

import re

_TYPESCRIPT_LANGUAGE_RE = re.compile(
    r"(?im)^\s*[-*]?\s*(?:主语言|main language|language)\s*[:：]\s*"
    r"(?:typescript|javascript|node(?:\.js)?|ts|js)\b"
)
_TYPESCRIPT_GOAL_RE = re.compile(
    r"(?i)(?:用\s*(?:typescript|javascript|node(?:\.js)?)\s*实现|"
    r"implement(?:ed)?\s+in\s+(?:typescript|javascript|node(?:\.js)?))"
)
_NON_TYPESCRIPT_LANGUAGE_RE = re.compile(
    r"(?im)^\s*[-*]?\s*(?:主语言|main language|language)\s*[:：]\s*"
    r"(?:python|go|golang|rust|java|c\+\+|cpp|c#|csharp|kotlin|swift|php|shell|bash|sql)\b"
)
_TYPESCRIPT_PATH_RE = re.compile(r"(?i)(?:^|[\s`'\"(:])[\w./*{}-]+\.(?:ts|tsx)\b")


def directive_requires_typescript_package_contract(directive: str) -> bool:
    """Return whether a directive truly requires the TypeScript/npm factory gate."""

    text = str(directive or "")
    lower = text.lower()
    has_package_contract = any(
        token in lower
        for token in (
            "package.json",
            "npm",
            "build/test/start",
            "build, test, and start",
            "build/test",
        )
    )
    if not has_package_contract:
        return False

    explicit_typescript = bool(_TYPESCRIPT_LANGUAGE_RE.search(text) or _TYPESCRIPT_GOAL_RE.search(text))
    if explicit_typescript:
        return True

    has_non_typescript_contract = bool(_NON_TYPESCRIPT_LANGUAGE_RE.search(text)) or any(
        token in lower
        for token in (
            "py_compile",
            "requirements.txt",
            "python -m",
            "pytest",
            "unittest",
            "source_target_coverage:src/**/*.py",
            ".py",
            "go_compile",
            "go.mod",
            "golang",
            "source_target_coverage:src/**/*.go",
            ".go",
            "rust_compile",
            "cargo.toml",
            "source_target_coverage:src/**/*.rs",
            ".rs",
            "java_compile",
            "src/main/java",
            ".java",
            "cpp_compile",
            "c++17",
            ".cpp",
            ".hpp",
        )
    )
    if has_non_typescript_contract:
        return False

    return any(
        (
            "typescript" in lower,
            "javascript" in lower,
            "node.js" in lower,
            "ts_syntax" in lower,
            "source_target_coverage:src/**/*.ts" in lower,
            "source_target_coverage:src/**/*.tsx" in lower,
            bool(_TYPESCRIPT_PATH_RE.search(text)),
        )
    )
