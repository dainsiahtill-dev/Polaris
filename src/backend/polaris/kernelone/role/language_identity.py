"""Language-specific professional identity helpers for role composition.

This module is intentionally neutral KernelOne infrastructure. It performs no
file I/O and owns no Director tasking behavior; callers provide text, metadata,
or target paths and receive an optional professional identity override.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class LanguageProfessionalIdentity:
    """Profession override selected from task language signals."""

    language: str
    display_name: str
    profession_name: str
    identity: str
    source: str = "kernelone.role.language_identity"

    @property
    def cache_token(self) -> str:
        return f"{self.language}:{self.profession_name}"


_LANG_ALIASES: dict[str, str] = {
    "golang": "go",
    "py": "python",
    "python3": "python",
    "ts": "typescript",
    "tsx": "typescript",
    "js": "javascript",
    "jsx": "javascript",
    "node": "javascript",
    "nodejs": "javascript",
    "rs": "rust",
    "kt": "kotlin",
    "kts": "kotlin",
    "objc": "objectivec",
    "objective-c": "objectivec",
    "objective_c": "objectivec",
    "cs": "csharp",
    "c#": "csharp",
    "bash": "shell",
    "sh": "shell",
}

_LANGUAGE_IDENTITIES: dict[str, LanguageProfessionalIdentity] = {
    "go": LanguageProfessionalIdentity(
        language="go",
        display_name="Go (Golang)",
        profession_name="Go (Golang) 资深软件架构师",
        identity=(
            "你是一位精通 Go (Golang) 语言的资深软件架构师，严格遵守官方 Effective Go、"
            "Go Code Review Comments 和工业级高性能服务端工程规范。"
        ),
    ),
    "python": LanguageProfessionalIdentity(
        language="python",
        display_name="Python",
        profession_name="Python 资深架构师",
        identity="你是一位资深 Python 架构师，严格遵守 PEP 8、现代类型提示、pytest 实践和可维护服务端工程规范。",
    ),
    "typescript": LanguageProfessionalIdentity(
        language="typescript",
        display_name="TypeScript",
        profession_name="TypeScript 前端/全栈架构师",
        identity=(
            "你是一位资深 TypeScript 前端/全栈架构师，遵循 TypeScript Handbook、strict mode、"
            "ESLint/Prettier 和 React/Node 工程化规范。"
        ),
    ),
    "javascript": LanguageProfessionalIdentity(
        language="javascript",
        display_name="JavaScript",
        profession_name="JavaScript/Node.js 资深工程师",
        identity="你是一位资深 JavaScript/Node.js 工程师，遵循 Airbnb/StandardJS 风格、ES2022+、现代模块化和 Node/Web 工程实践。",
    ),
    "rust": LanguageProfessionalIdentity(
        language="rust",
        display_name="Rust",
        profession_name="Rust 系统工程师",
        identity="你是一位精通 Rust 的系统工程师，严格遵守 Rust API Guidelines、所有权模型和并发安全实践。",
    ),
    "ruby": LanguageProfessionalIdentity(
        language="ruby",
        display_name="Ruby",
        profession_name="Ruby 应用架构师",
        identity="你是一位资深 Ruby 应用架构师，遵循 Ruby Style Guide、RuboCop、清晰对象边界和可测试服务对象实践。",
    ),
    "java": LanguageProfessionalIdentity(
        language="java",
        display_name="Java",
        profession_name="Java 企业级架构师",
        identity="你是一位精通 Java 的企业级架构师，遵循 Google Java Style、清晰分层和类型安全实践。",
    ),
    "kotlin": LanguageProfessionalIdentity(
        language="kotlin",
        display_name="Kotlin",
        profession_name="Kotlin 应用架构师",
        identity="你是一位资深 Kotlin 应用架构师，遵循 Kotlin Coding Conventions、空安全、协程和表达式化建模实践。",
    ),
    "swift": LanguageProfessionalIdentity(
        language="swift",
        display_name="Swift",
        profession_name="Swift Apple 平台工程师",
        identity="你是一位精通 Swift 的资深 Apple 平台工程师，遵循 Swift API Design Guidelines 和值语义实践。",
    ),
    "c": LanguageProfessionalIdentity(
        language="c",
        display_name="C",
        profession_name="C 系统工程师",
        identity="你是一位精通 C 的系统工程师，遵循 CERT C、显式内存所有权和可移植性实践。",
    ),
    "cpp": LanguageProfessionalIdentity(
        language="cpp",
        display_name="C++",
        profession_name="C++17/20 系统工程师",
        identity="你是一位精通 C++17/20 的系统工程师，遵循 C++ Core Guidelines、RAII 和现代类型安全实践。",
    ),
    "csharp": LanguageProfessionalIdentity(
        language="csharp",
        display_name="C#",
        profession_name="C#/.NET 架构师",
        identity="你是一位资深 C#/.NET 架构师，遵循 Microsoft C# Coding Conventions、异步、nullable reference types 和清晰服务边界实践。",
    ),
    "php": LanguageProfessionalIdentity(
        language="php",
        display_name="PHP",
        profession_name="PHP 8+ 资深工程师",
        identity="你是一位精通 PHP 8+ 的资深工程师，遵循 PSR-12、Composer 和现代类型声明实践。",
    ),
    "shell": LanguageProfessionalIdentity(
        language="shell",
        display_name="Shell/Bash",
        profession_name="Shell/Bash DevOps 工程师",
        identity="你是一位精通 Shell/Bash 的 DevOps 工程师，遵循 Google Shell Style Guide 和安全脚本实践。",
    ),
    "sql": LanguageProfessionalIdentity(
        language="sql",
        display_name="SQL",
        profession_name="SQL/数据库迁移架构师",
        identity="你是一位资深 SQL/数据库迁移架构师，遵循项目 SQL style guide、约束优先、索引、事务和可回滚变更实践。",
    ),
    "html": LanguageProfessionalIdentity(
        language="html",
        display_name="HTML",
        profession_name="语义化 HTML/WCAG 前端工程师",
        identity="你是一位资深语义化 HTML 和 WCAG 可访问性前端工程师，重视结构、表单语义和渐进增强。",
    ),
    "css": LanguageProfessionalIdentity(
        language="css",
        display_name="CSS",
        profession_name="CSS 架构和 UI 用户体验工程师",
        identity="你是一位资深 CSS 架构和 UI 用户体验工程师，遵循 CSS Guidelines、响应式、可维护选择器和布局稳定性实践。",
    ),
}

_EXTENSION_LANGUAGE: dict[str, str] = {
    ".go": "go",
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".sh": "shell",
    ".bash": "shell",
    ".sql": "sql",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "css",
    ".sass": "css",
    ".less": "css",
}

_BASENAME_LANGUAGE: dict[str, str] = {
    "go.mod": "go",
    "go.sum": "go",
    "cargo.toml": "rust",
    "cargo.lock": "rust",
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "package.json": "javascript",
    "tsconfig.json": "typescript",
    "dockerfile": "shell",
}

_TEXT_PATTERNS: dict[str, tuple[str, ...]] = {
    "go": (r"\bgolang\b", r"\bgo\s+(?:service|module|package|handler|test|code)\b"),
    "python": (r"\bpython(?:3)?\b", r"\bpytest\b", r"\bfastapi\b", r"\bdjango\b"),
    "typescript": (r"\btypescript\b", r"\btsx\b", r"\btsconfig\b"),
    "javascript": (r"\bjavascript\b", r"\bnode(?:\.js|js)?\b", r"\bnpm\b"),
    "rust": (r"\brust\b", r"\bcargo\b", r"\bcrate\b"),
    "java": (r"\bjava\b", r"\bspring\b", r"\bgradle\b", r"\bpom\.xml\b"),
    "kotlin": (r"\bkotlin\b",),
    "swift": (r"\bswift\b",),
    "cpp": (r"\bc\+\+\b", r"\bcpp\b"),
    "csharp": (r"\bc#\b", r"\bcsharp\b", r"\b\.net\b"),
    "php": (r"\bphp\b", r"\bcomposer\b"),
    "ruby": (r"\bruby\b", r"\brspec\b"),
    "shell": (r"\bbash\b", r"\bshell script\b"),
    "sql": (r"\bsql\b", r"\bpostgres\b", r"\bmysql\b", r"\bsqlite\b"),
    "html": (r"\bhtml\b", r"\bwcag\b"),
    "css": (r"\bcss\b", r"\bscss\b", r"\bsass\b"),
}


def normalize_language_token(value: Any) -> str:
    """Return a canonical language token, or an empty string."""

    token = re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())
    if not token:
        return ""
    return _LANG_ALIASES.get(token, token)


def get_language_professional_identity(language: Any) -> LanguageProfessionalIdentity | None:
    """Return a language professional identity for a canonical or aliased token."""

    return _LANGUAGE_IDENTITIES.get(normalize_language_token(language))


def infer_language_professional_identity(
    *,
    text: str = "",
    target_files: Sequence[str] = (),
    metadata: Mapping[str, Any] | None = None,
) -> LanguageProfessionalIdentity | None:
    """Infer a professional identity override from metadata, paths, or prompt text."""

    language = _language_from_metadata(metadata or {})
    if not language:
        language = _language_from_paths(tuple(target_files) or _extract_paths_from_text(text))
    if not language:
        language = _language_from_text(text)
    return get_language_professional_identity(language)


def _language_from_metadata(metadata: Mapping[str, Any]) -> str:
    for key in ("detected_language", "language", "primary_language", "programming_language"):
        language = normalize_language_token(metadata.get(key))
        if language in _LANGUAGE_IDENTITIES:
            return language
    tech_stack = metadata.get("tech_stack")
    if isinstance(tech_stack, Mapping):
        return _language_from_metadata(tech_stack)
    return ""


def _language_from_paths(paths: Sequence[str]) -> str:
    counts: dict[str, int] = {}
    for raw_path in paths:
        normalized = str(raw_path or "").strip().replace("\\", "/").lower()
        if not normalized:
            continue
        basename = Path(normalized).name
        language = _BASENAME_LANGUAGE.get(basename)
        if not language:
            language = _EXTENSION_LANGUAGE.get(Path(normalized).suffix.lower(), "")
        if language:
            counts[language] = counts.get(language, 0) + 1
    if not counts:
        return ""
    return max(counts, key=lambda item: (counts[item], item))


def _language_from_text(text: str) -> str:
    normalized = str(text or "").lower()
    if not normalized:
        return ""
    scores: dict[str, int] = {}
    for language, patterns in _TEXT_PATTERNS.items():
        score = sum(1 for pattern in patterns if re.search(pattern, normalized))
        if score:
            scores[language] = score
    if not scores:
        return ""
    return max(scores, key=lambda item: (scores[item], item))


def _extract_paths_from_text(text: str) -> tuple[str, ...]:
    normalized = str(text or "")
    if not normalized:
        return ()
    candidates = re.findall(
        r"[\w./\\@-]+(?:\.go|\.pyi?|\.tsx?|\.jsx?|\.mjs|\.cjs|\.rs|\.rb|\.java|\.kts?|\.swift|"
        r"\.c|\.cc|\.cpp|\.cxx|\.hpp|\.hh|\.hxx|\.cs|\.php|\.sh|\.bash|\.sql|\.html?|\.s?css|"
        r"\.sass|\.less)\b",
        normalized,
        flags=re.IGNORECASE,
    )
    for basename in _BASENAME_LANGUAGE:
        if re.search(rf"(?<![\w.-]){re.escape(basename)}(?![\w.-])", normalized, flags=re.IGNORECASE):
            candidates.append(basename)
    return tuple(candidates)
