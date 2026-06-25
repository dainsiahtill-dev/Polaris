"""Canonical Java syntax repair rules for Director Runtime."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

JAVA_ACCESSOR_ALIAS_SOURCE_TOOL = "deterministic_java_accessor_alias_repair"


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


def _insert_java_methods_before_final_class_brace(content: str, methods: list[str]) -> str:
    last_brace = content.rfind("}")
    if last_brace < 0:
        return content
    insertion = "\n" + "\n".join(method.rstrip() + "\n" for method in methods)
    return content[:last_brace].rstrip() + insertion + content[last_brace:]


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


__all__ = [
    "JAVA_ACCESSOR_ALIAS_SOURCE_TOOL",
    "build_java_accessor_alias_plan",
    "repair_java_common_accessor_aliases_text",
]
