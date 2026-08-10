"""Language / package-contract detection helpers for PM synthesis."""

from __future__ import annotations

import re

from ..language_contracts import directive_requires_typescript_package_contract
from ..pm_text_utils import _pm_root_workspace_contract_targets_from_directive


def _directive_requires_typescript_package_contract(directive: str) -> bool:
    return directive_requires_typescript_package_contract(directive)


def _explicit_primary_language_from_directive(directive: str) -> str:
    text = str(directive or "")
    match = re.search(
        r"(?im)^\s*[-*]?\s*(?:主语言|main language)\s*[:：]\s*([A-Za-z0-9_+#.-]+)\s*(?:$|[;,.，。])",
        text,
    )
    if not match:
        return ""
    token = match.group(1).strip().lower()
    aliases = {
        "golang": "go",
        "js": "javascript",
        "node": "javascript",
        "nodejs": "javascript",
        "node.js": "javascript",
        "ts": "typescript",
        "py": "python",
        "c++": "cpp",
    }
    return aliases.get(token, token)


def _directive_has_other_explicit_primary_language(directive: str, *expected: str) -> bool:
    primary = _explicit_primary_language_from_directive(directive)
    if not primary:
        return False
    return primary not in {item.lower() for item in expected}


def _directive_requires_rust_package_contract(directive: str) -> bool:
    if _directive_has_other_explicit_primary_language(directive, "rust"):
        return False
    lower = str(directive or "").lower()
    return any(
        token in lower
        for token in (
            "rust",
            "cargo",
            ".rs",
            "rust_compile",
            "source_target_coverage:src/**/*.rs",
        )
    )


def _directive_requires_cpp_package_contract(directive: str) -> bool:
    if _directive_has_other_explicit_primary_language(directive, "cpp"):
        return False
    lower = str(directive or "").lower()
    return any(
        token in lower
        for token in (
            "c++",
            "cpp",
            "c++17",
            ".cpp",
            ".hpp",
            "cpp_compile",
            "source_target_coverage:src/**/*.cpp",
        )
    )


def _directive_requires_go_workspace_contract(directive: str) -> bool:
    if _directive_has_other_explicit_primary_language(directive, "go"):
        return False
    text = str(directive or "")
    lower = text.lower()
    has_explicit_go_metadata = bool(
        re.search(r"(?im)^\s*[-*]?\s*(?:主语言|main language)\s*[:：]\s*go\s*(?:$|[;,.，。])", text)
    )
    has_explicit_go_goal = bool(re.search(r"(?i)(?:用\s+go\s+实现|implement(?:ed)?\s+in\s+go)", text))
    has_explicit_go_artifact = any(
        token in lower
        for token in (
            ".go",
            "go.mod",
            "go test",
            "go run",
            "go_compile",
            "source_target_coverage:**/*.go",
            "source_target_coverage:src/**/*.go",
        )
    )
    return has_explicit_go_metadata or has_explicit_go_goal or has_explicit_go_artifact


def _directive_requires_java_package_contract(directive: str) -> bool:
    if _directive_has_other_explicit_primary_language(directive, "java"):
        return False
    text = str(directive or "")
    lower = str(directive or "").lower()
    has_explicit_java_metadata = bool(
        re.search(r"(?im)^\s*[-*]?\s*(?:主语言|main language)\s*[:：]\s*java\s*(?:$|[;,.，。])", text)
    )
    has_explicit_java_artifact = any(
        token in lower
        for token in (
            ".java",
            "javac",
            "java_compile",
            "src/main/java",
            "source_target_coverage:src/main/java",
        )
    )
    return (
        has_explicit_java_metadata
        or has_explicit_java_artifact
        or bool(re.search(r"(?<!script)\bjava\b(?!script)", lower))
    )


def _directive_requires_javascript_package_contract(directive: str) -> bool:
    explicit_primary_language = _explicit_primary_language_from_directive(directive)
    if explicit_primary_language and explicit_primary_language != "javascript":
        return False
    lower = str(directive or "").lower()
    if (
        _directive_requires_rust_package_contract(directive)
        or _directive_requires_cpp_package_contract(directive)
        or _directive_requires_go_workspace_contract(directive)
        or _directive_requires_java_package_contract(directive)
        or _directive_requires_python_workspace_contract(directive)
    ):
        return False
    has_javascript = any(
        token in lower
        for token in (
            "主语言: javascript",
            "主语言: js",
            "主语言: node",
            "主语言: node.js",
            "main language: javascript",
            "main language: js",
            "main language: node",
            "main language: node.js",
            "用 javascript 实现",
            "用 js 实现",
            "用 node.js 实现",
            "js_syntax",
            "source_target_coverage:src/**/*.js",
            "src/index.js",
        )
    )
    has_package_contract = any(
        token in lower
        for token in (
            "package.json",
            "package_scripts",
            "npm",
            "build/test/start",
            "build, test, and start",
            "build/test",
        )
    )
    return has_javascript and has_package_contract


def _directive_requires_python_workspace_contract(directive: str) -> bool:
    if _directive_has_other_explicit_primary_language(directive, "python"):
        return False
    lower = str(directive or "").lower()
    return any(
        token in lower
        for token in (
            "主语言: python",
            "main language: python",
            "用 python 实现",
            "py_compile",
            "source_target_coverage:src/**/*.py",
        )
    )


def _directive_requires_language_root_delivery_contract(directive: str) -> bool:
    root_workspace_targets = _pm_root_workspace_contract_targets_from_directive(directive)
    if not root_workspace_targets:
        return False
    source_file, _, _ = root_workspace_targets
    if source_file != "index.html":
        return False
    return any(
        (
            _directive_requires_typescript_package_contract(directive),
            _directive_requires_javascript_package_contract(directive),
            _directive_requires_java_package_contract(directive),
            _directive_requires_python_workspace_contract(directive),
            _directive_requires_go_workspace_contract(directive),
            _directive_requires_rust_package_contract(directive),
            _directive_requires_cpp_package_contract(directive),
        )
    )
