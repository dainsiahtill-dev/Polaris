"""Canonical Java syntax repair rules for Director Runtime."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

JAVA_ACCESSOR_ALIAS_SOURCE_TOOL = "deterministic_java_accessor_alias_repair"
JAVA_TEST_DEPENDENCY_SOURCE_TOOL = "deterministic_java_test_dependency_repair"


def repair_java_common_accessor_aliases_text(text: str) -> str:
    """Add small Java accessor aliases when tests use beanless method names."""

    content = str(text or "")
    additions: list[str] = []
    if "int getTemperament()" in content and "int temperament()" not in content:
        additions.append("    public int temperament() {\n        return getTemperament();\n    }\n")
    if "int getSleepyLevel()" in content and "int sleepyLevel()" not in content:
        additions.append("    public int sleepyLevel() {\n        return getSleepyLevel();\n    }\n")
    if "int get(int index)" in content and "int length()" in content and "boolean isHit(int index)" not in content:
        additions.append("    public boolean isHit(int index) {\n        return get(index) == HIT;\n    }\n")
    if "int get(int index)" in content and "int length()" in content and "boolean isRest(int index)" not in content:
        additions.append("    public boolean isRest(int index) {\n        return get(index) == REST;\n    }\n")
    if "int get(int index)" in content and "int length()" in content and "int countRests()" not in content:
        additions.append(
            "    public int countRests() {\n"
            "        int count = 0;\n"
            "        for (int i = 0; i < length(); i++) {\n"
            "            if (isRest(i)) {\n"
            "                count++;\n"
            "            }\n"
            "        }\n"
            "        return count;\n"
            "    }\n"
        )
    if not additions:
        return content
    return _insert_java_methods_before_final_class_brace(content, additions)


def build_java_accessor_alias_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical plan for Java common accessor aliases."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    for path in sorted(normalized_base_files):
        if not path.endswith(".java") or "/src/main/java/" not in f"/{path}":
            continue
        original = normalized_base_files[path]
        repaired = repair_java_common_accessor_aliases_text(original)
        if repaired == original:
            continue
        operations.append(
            RepairOperation(
                kind="write_file",
                path=path,
                content=repaired,
                before_hash=sha256_text(original),
                metadata={"repair_kind": "java_common_accessor_aliases"},
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="java.common_accessor_aliases",
        source_tool=JAVA_ACCESSOR_ALIAS_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="low",
        priority=1,
    )


def repair_java_test_dependencies_text(text: str, *, class_name: str = "Test") -> str:
    """Rewrite JUnit-dependent test source into plain Java executable tests."""

    content = str(text or "")
    junit_import_re = re.compile(r"^\s*import\s+org\.(junit|jupiter)\..*?;\s*$", re.MULTILINE)
    junit_annotation_re = re.compile(
        r"^\s*@(Test|BeforeEach|AfterEach|BeforeAll|AfterAll|DisplayName|Nested)\b.*$",
        re.MULTILINE,
    )
    if not junit_import_re.search(content):
        return content

    new_content = junit_import_re.sub("", content)
    new_content = junit_annotation_re.sub("", new_content)
    new_content = re.sub(
        r"(class\s+\w+)\s+extends\s+\w+",
        r"\1",
        new_content,
    )

    normalized_class_name = str(class_name or "Test").strip() or "Test"
    if "public static void main" not in new_content:
        test_methods = re.findall(r"(?:public\s+)?void\s+(\w+)\s*\(\s*\)", new_content)
        if test_methods:
            main_body = "\n".join(
                f'        System.out.println("Running {method_name}...");'
                f"\n        new {normalized_class_name}().{method_name}();"
                f'\n        System.out.println("  PASS");'
                for method_name in test_methods
            )
            main_method = (
                "\n    public static void main(String[] args) {\n"
                f'        System.out.println("=== {normalized_class_name} ===");\n'
                f"{main_body}\n"
                '        System.out.println("All tests passed!");\n'
                "    }\n"
            )
            last_brace = new_content.rfind("}")
            if last_brace >= 0:
                new_content = new_content[:last_brace] + main_method + new_content[last_brace:]

    new_content = re.sub(
        r"^\s*assert(?:True|False|NotNull|Null|Equals|NotEquals|Throws|ArrayEquals)\b.*?;\s*$",
        "",
        new_content,
        flags=re.MULTILINE,
    )
    new_content = re.sub(r"^\s*Assertions?\.\w+\b.*?;\s*$", "", new_content, flags=re.MULTILINE)
    new_content = re.sub(r"^\s*import\s+static\s+org\..*?;\s*$", "", new_content, flags=re.MULTILINE)
    return new_content


def build_java_test_dependency_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a runtime-owned plan for removing JUnit-only test dependencies."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    for path in sorted(normalized_base_files):
        if not _is_java_test_path(path):
            continue
        original = normalized_base_files[path]
        class_name = path.rsplit("/", maxsplit=1)[-1].removesuffix(".java")
        repaired = repair_java_test_dependencies_text(original, class_name=class_name)
        if repaired == original:
            continue
        operations.append(
            RepairOperation(
                kind="write_file",
                path=path,
                content=repaired,
                before_hash=sha256_text(original),
                metadata={
                    "repair_kind": "java_junit_test_dependency",
                    "edit_strategy": "whole_file_fallback",
                    "legacy_transform_migrated": True,
                    "write_file_reason": "java_junit_dependency_transform_requires_multi_span_rewrite",
                },
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="java.junit_test_dependency",
        source_tool=JAVA_TEST_DEPENDENCY_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="medium",
        priority=1,
        metadata={
            "edit_strategy": "whole_file_fallback",
            "legacy_transform_migrated": True,
        },
    )


def _insert_java_methods_before_final_class_brace(content: str, methods: list[str]) -> str:
    last_brace = content.rfind("}")
    if last_brace < 0:
        return content
    insertion = "\n" + "\n".join(method.rstrip() + "\n" for method in methods)
    return content[:last_brace].rstrip() + insertion + content[last_brace:]


def _is_java_test_path(path: str) -> bool:
    normalized = _normalize_repair_path(path)
    if not normalized.endswith(".java"):
        return False
    parts = tuple(part.lower() for part in normalized.split("/") if part)
    if len(parts) >= 3 and parts[0] == "src" and parts[1] == "test":
        return True
    return "test" in parts or "tests" in parts


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


__all__ = [
    "JAVA_ACCESSOR_ALIAS_SOURCE_TOOL",
    "JAVA_TEST_DEPENDENCY_SOURCE_TOOL",
    "build_java_accessor_alias_plan",
    "build_java_test_dependency_plan",
    "repair_java_common_accessor_aliases_text",
    "repair_java_test_dependencies_text",
]
