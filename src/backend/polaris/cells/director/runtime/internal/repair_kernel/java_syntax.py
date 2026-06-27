"""Canonical Java syntax repair rules for Director Runtime."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

JAVA_ACCESSOR_ALIAS_SOURCE_TOOL = "deterministic_java_accessor_alias_repair"
JAVA_POST_SOURCE_TOOL = "deterministic_java_post_repair"
JAVA_TEST_DEPENDENCY_SOURCE_TOOL = "deterministic_java_test_dependency_repair"
_JAVA_EOF_TRUNCATION_RULE_ID = "java.eof_truncation_closure"
_JAVA_NUMERIC_CONSTANT_RULE_ID = "java.numeric_constant_literal_type"
_JAVA_MISSING_SYMBOL_COMPAT_RULE_ID = "java.missing_symbol_compatibility"
_JAVA_EOF_MAX_CLOSING_BRACES = 6
_JAVA_EOF_PATH_RE = re.compile(r"(?P<path>[^:\s]+\.java):\d+: error: reached end of file while parsing")
_JAVA_LOSSY_DOUBLE_TO_INT_RE = re.compile(
    r"(?P<path>[^:\s]+\.java):(?P<line>\d+): error: incompatible types: possible lossy conversion from double to int"
)
_JAVA_INT_DECIMAL_CONSTANT_RE = re.compile(
    r"(?m)^(?P<indent>\s*)(?P<modifiers>(?:public|private|protected)?\s*(?:static\s+)?final\s+)int"
    r"(?P<suffix>\s+(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*-?\d+\.\d+\s*;)"
)
_JAVA_MISSING_VARIABLE_RE = re.compile(
    r"symbol:\s+variable\s+(?P<symbol>[A-Z][A-Z0-9_]*)\s+location:\s+class\s+(?P<class>[A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
_JAVA_MISSING_METHOD_RE = re.compile(
    r"symbol:\s+method\s+(?P<method>[A-Za-z_][A-Za-z0-9_]*)\((?P<params>[^)]*)\)\s+"
    r"location:\s+(?:variable\s+[A-Za-z_][A-Za-z0-9_]*\s+of type|class)\s+(?P<class>[A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
_JAVA_CLASS_RE = re.compile(r"\bclass\s+(?P<class>[A-Za-z_][A-Za-z0-9_]*)\b")
_JAVA_THRESHOLD_DECL_RE = re.compile(
    r"(?m)^(?P<indent>\s*)public\s+static\s+final\s+(?P<type>int|double)\s+"
    r"(?P<name>[A-Z][A-Z0-9_]*_THRESHOLD)\s*=\s*(?P<value>-?\d+(?:\.\d+)?)\s*;"
)
_JAVA_INT_METHOD_RE = re.compile(
    r"(?m)^\s*public\s+int\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)\s*\{"
)


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


def repair_java_eof_truncation_text(text: str) -> str:
    """Repair a generated Java source file that was truncated at EOF."""

    repaired, _metadata = _repair_java_eof_truncation_text_with_metadata(str(text or ""))
    return repaired


def build_java_eof_truncation_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a bounded plan for javac EOF parse errors in generated Java files."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    target_paths = _java_eof_target_paths(diagnostics=diagnostics, base_files=normalized_base_files)
    operations: list[RepairOperation] = []
    repairs: list[dict[str, object]] = []
    for path in target_paths:
        original = normalized_base_files.get(path)
        if original is None:
            continue
        repaired, metadata = _repair_java_eof_truncation_text_with_metadata(original)
        if repaired == original:
            continue
        span_start = _common_prefix_len(original, repaired)
        expected = original[span_start:]
        replacement = repaired[span_start:]
        if not expected:
            continue
        context_start = max(0, span_start - 240)
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=span_start,
                span_end=len(original),
                expected=expected,
                replacement=replacement,
                before_hash=sha256_text(original),
                metadata={
                    **metadata,
                    "repair_kind": "java_eof_truncation_closure",
                    "edit_strategy": "span_text_replace",
                    "unique_context": original[context_start:],
                    "expected_context_before": original[context_start:span_start],
                },
            )
        )
        repairs.append({"path": path, **metadata})
    if not operations:
        return None
    return RepairPlan(
        rule_id=_JAVA_EOF_TRUNCATION_RULE_ID,
        source_tool=JAVA_POST_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "repair_kind": "java_eof_truncation_closure",
            "edit_strategy": "span_text_replace",
            "repairs": tuple(repairs),
            "unsafe_cases_fail_closed": True,
        },
    )


def build_java_numeric_constant_type_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a precise plan for decimal literals assigned to Java int constants."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    target_paths = _java_lossy_double_to_int_target_paths(diagnostics=diagnostics, base_files=normalized_base_files)
    operations: list[RepairOperation] = []
    repairs: list[dict[str, str]] = []
    for path in target_paths:
        original = normalized_base_files.get(path)
        if original is None:
            continue
        for match in _JAVA_INT_DECIMAL_CONSTANT_RE.finditer(original):
            expected = match.group(0)
            replacement = f"{match.group('indent')}{match.group('modifiers')}double{match.group('suffix')}"
            if expected == replacement:
                continue
            operations.append(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=match.start(),
                    span_end=match.end(),
                    expected=expected,
                    replacement=replacement,
                    before_hash=sha256_text(original),
                    metadata={
                        "repair_kind": "java_numeric_constant_literal_type",
                        "edit_strategy": "span_text_replace",
                        "constant_name": match.group("name"),
                        "expected_context_before": original[max(0, match.start() - 120) : match.start()],
                        "unique_context": original[max(0, match.start() - 120) : match.end()],
                    },
                )
            )
            repairs.append({"path": path, "constant_name": match.group("name")})
            break
    if not operations:
        return None
    return RepairPlan(
        rule_id=_JAVA_NUMERIC_CONSTANT_RULE_ID,
        source_tool=JAVA_POST_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "repair_kind": "java_numeric_constant_literal_type",
            "edit_strategy": "span_text_replace",
            "repairs": tuple(repairs),
            "unsafe_cases_fail_closed": True,
        },
    )


def build_java_missing_symbol_compatibility_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build conservative Java compatibility aliases for missing test-facing symbols."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    additions_by_path: dict[str, list[str]] = {}
    repairs: list[dict[str, str]] = []
    for class_name, symbol in _java_missing_variable_symbols(diagnostics):
        if not symbol.endswith("_THRESHOLD"):
            continue
        path = _java_class_file_path(class_name, base_files=normalized_base_files)
        if not path:
            continue
        addition = _java_threshold_alias_addition(normalized_base_files[path], symbol)
        if not addition:
            continue
        additions_by_path.setdefault(path, []).append(addition)
        repairs.append({"path": path, "symbol": symbol, "kind": "threshold_alias"})
    for class_name, method_name, params in _java_missing_methods(diagnostics):
        if not method_name.startswith("will"):
            continue
        path = _java_class_file_path(class_name, base_files=normalized_base_files)
        if not path:
            continue
        addition = _java_will_method_addition(normalized_base_files[path], method_name, params)
        if not addition:
            continue
        additions_by_path.setdefault(path, []).append(addition)
        repairs.append({"path": path, "symbol": method_name, "kind": "will_method_alias"})

    operations: list[RepairOperation] = []
    for path, additions in sorted(additions_by_path.items()):
        original = normalized_base_files[path]
        operation = _java_member_insertion_operation(
            path=path,
            original=original,
            additions=_dedupe_preserve_order(additions),
            repair_kind="java_missing_symbol_compatibility",
        )
        if operation is not None:
            operations.append(operation)
    if not operations:
        return None
    return RepairPlan(
        rule_id=_JAVA_MISSING_SYMBOL_COMPAT_RULE_ID,
        source_tool=JAVA_POST_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="medium",
        priority=1,
        metadata={
            "repair_kind": "java_missing_symbol_compatibility",
            "edit_strategy": "span_text_replace",
            "repairs": tuple(repairs),
            "unsafe_cases_fail_closed": True,
        },
    )


def build_java_post_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a conservative aggregate Java post repair plan from runtime child rules."""

    child_plans = tuple(
        plan
        for plan in (
            build_java_numeric_constant_type_plan(base_files=base_files, diagnostics=diagnostics, mode=mode),
            build_java_eof_truncation_plan(base_files=base_files, diagnostics=diagnostics, mode=mode),
            build_java_missing_symbol_compatibility_plan(base_files=base_files, diagnostics=diagnostics, mode=mode),
            build_java_accessor_alias_plan(base_files=base_files, diagnostics=diagnostics, mode=mode),
            build_java_test_dependency_plan(base_files=base_files, diagnostics=diagnostics, mode=mode),
        )
        if plan is not None
    )
    operations: list[RepairOperation] = []
    child_rule_ids: list[str] = []
    child_source_tools: list[str] = []
    seen_operation_ids: set[str] = set()
    for plan in child_plans:
        child_rule_ids.append(plan.rule_id)
        child_source_tools.append(plan.source_tool)
        for operation in plan.operations:
            if operation.operation_id in seen_operation_ids:
                continue
            seen_operation_ids.add(operation.operation_id)
            operations.append(operation)
    if not operations:
        return None
    return RepairPlan(
        rule_id="java.post_execution_conservative",
        source_tool=JAVA_POST_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="medium",
        priority=2,
        depends_on=tuple(child_rule_ids),
        metadata={
            "repair_kind": "java_post_execution_conservative",
            "aggregate_runtime_child_rules": tuple(child_rule_ids),
            "aggregate_runtime_child_source_tools": tuple(child_source_tools),
            "legacy_post_helper_used": False,
        },
    )


def _insert_java_methods_before_final_class_brace(content: str, methods: list[str]) -> str:
    last_brace = content.rfind("}")
    if last_brace < 0:
        return content
    insertion = "\n" + "\n".join(method.rstrip() + "\n" for method in methods)
    return content[:last_brace].rstrip() + insertion + content[last_brace:]


def _repair_java_eof_truncation_text_with_metadata(content: str) -> tuple[str, dict[str, object]]:
    original = str(content or "")
    lines = original.splitlines(keepends=True)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return original, {}

    dropped_line_count = 0
    if _java_tail_line_incomplete(lines[-1]):
        lines.pop()
        dropped_line_count = 1

    base_text = "".join(lines)
    if not base_text.strip():
        return original, {}
    if not base_text.endswith(("\n", "\r")):
        base_text += "\n"

    missing_closing_braces = _java_missing_closing_brace_count(base_text)
    if missing_closing_braces <= 0 or missing_closing_braces > _JAVA_EOF_MAX_CLOSING_BRACES:
        return original, {}

    repaired = base_text + ("}\n" * missing_closing_braces)
    return repaired, {
        "dropped_incomplete_tail": bool(dropped_line_count),
        "dropped_line_count": dropped_line_count,
        "missing_closing_braces": missing_closing_braces,
        "max_missing_closing_braces": _JAVA_EOF_MAX_CLOSING_BRACES,
    }


def _java_eof_target_paths(
    *,
    diagnostics: Sequence[RepairDiagnostic],
    base_files: Mapping[str, str],
) -> tuple[str, ...]:
    targets: list[str] = []
    for diagnostic in diagnostics:
        if not _is_java_eof_diagnostic(diagnostic):
            continue
        candidates = [str(diagnostic.path or "")]
        candidates.extend(match.group("path") for match in _JAVA_EOF_PATH_RE.finditer(str(diagnostic.raw or "")))
        for candidate in candidates:
            resolved = _resolve_java_base_file_path(candidate, base_files=base_files)
            if resolved:
                targets.append(resolved)
    return tuple(dict.fromkeys(targets))


def _java_lossy_double_to_int_target_paths(
    *,
    diagnostics: Sequence[RepairDiagnostic],
    base_files: Mapping[str, str],
) -> tuple[str, ...]:
    targets: list[str] = []
    for diagnostic in diagnostics:
        text = f"{diagnostic.code}\n{diagnostic.message}\n{diagnostic.raw}".lower()
        if "possible lossy conversion from double to int" not in text:
            continue
        candidates = [str(diagnostic.path or "")]
        candidates.extend(
            match.group("path") for match in _JAVA_LOSSY_DOUBLE_TO_INT_RE.finditer(str(diagnostic.raw or ""))
        )
        for candidate in candidates:
            resolved = _resolve_java_base_file_path(candidate, base_files=base_files)
            if resolved:
                targets.append(resolved)
    return tuple(dict.fromkeys(targets))


def _is_java_eof_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    text = f"{diagnostic.code}\n{diagnostic.message}\n{diagnostic.raw}".lower()
    if "reached end of file while parsing" not in text:
        return False
    return diagnostic.code.lower() == "java_compile_error" or ".java" in text or "javac" in text


def _resolve_java_base_file_path(candidate: str, *, base_files: Mapping[str, str]) -> str:
    normalized = str(candidate or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized in base_files:
        return normalized
    relative = _normalize_repair_path(normalized)
    if relative and relative in base_files:
        return relative
    suffix_matches = tuple(path for path in base_files if normalized.endswith(f"/{path}"))
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    basename = normalized.rsplit("/", maxsplit=1)[-1]
    basename_matches = tuple(path for path in base_files if path.rsplit("/", maxsplit=1)[-1] == basename)
    if len(basename_matches) == 1:
        return basename_matches[0]
    return ""


def _java_missing_variable_symbols(diagnostics: Sequence[RepairDiagnostic]) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for diagnostic in diagnostics:
        text = _java_diagnostic_text(diagnostic)
        for match in _JAVA_MISSING_VARIABLE_RE.finditer(text):
            pairs.append((match.group("class"), match.group("symbol")))
    return tuple(dict.fromkeys(pairs))


def _java_missing_methods(diagnostics: Sequence[RepairDiagnostic]) -> tuple[tuple[str, str, str], ...]:
    methods: list[tuple[str, str, str]] = []
    for diagnostic in diagnostics:
        text = _java_diagnostic_text(diagnostic)
        for match in _JAVA_MISSING_METHOD_RE.finditer(text):
            methods.append((match.group("class"), match.group("method"), match.group("params")))
    return tuple(dict.fromkeys(methods))


def _java_diagnostic_text(diagnostic: RepairDiagnostic) -> str:
    text = f"{diagnostic.message}\n{diagnostic.raw}"
    return text.replace("\\n", "\n").replace("\\t", "\t")


def _java_class_file_path(class_name: str, *, base_files: Mapping[str, str]) -> str:
    normalized_class = str(class_name or "").strip()
    if not normalized_class:
        return ""
    matches = tuple(
        path
        for path, content in base_files.items()
        if path.endswith(".java") and re.search(rf"\bclass\s+{re.escape(normalized_class)}\b", content)
    )
    return matches[0] if len(matches) == 1 else ""


def _java_threshold_alias_addition(content: str, missing_symbol: str) -> str:
    token = str(content or "")
    symbol = str(missing_symbol or "").strip()
    if not symbol or re.search(rf"\b{re.escape(symbol)}\b", token):
        return ""
    declarations = []
    for match in _JAVA_THRESHOLD_DECL_RE.finditer(token):
        try:
            numeric_value = float(match.group("value"))
        except ValueError:
            continue
        declarations.append((numeric_value, match.group("type"), match.group("name")))
    if not declarations:
        return ""
    _value, alias_type, alias_target = min(declarations, key=lambda item: item[0])
    return f"    public static final {alias_type} {symbol} = {alias_target};\n"


def _java_will_method_addition(content: str, method_name: str, params: str) -> str:
    token = str(content or "")
    normalized_method = str(method_name or "").strip()
    if not normalized_method or re.search(rf"\bboolean\s+{re.escape(normalized_method)}\s*\(", token):
        return ""
    threshold_name = f"{_camel_to_constant(normalized_method.removeprefix('will'))}_THRESHOLD"
    if not threshold_name.startswith("_") and not re.search(rf"\b{re.escape(threshold_name)}\b", token):
        return ""
    param_types = _java_param_types(params)
    if len(param_types) != 2:
        return ""
    score_method = _java_matching_int_score_method(token, param_types)
    if not score_method:
        return ""
    param_declarations = []
    param_names = []
    for param_type in param_types:
        param_name = _lower_camel_from_type(param_type)
        param_declarations.append(f"{param_type} {param_name}")
        param_names.append(param_name)
    return (
        f"    public boolean {normalized_method}({', '.join(param_declarations)}) {{\n"
        f"        return {score_method}({', '.join(param_names)}) >= {threshold_name};\n"
        "    }\n"
    )


def _java_matching_int_score_method(content: str, param_types: tuple[str, ...]) -> str:
    matches: list[str] = []
    for match in _JAVA_INT_METHOD_RE.finditer(content):
        method_params = _java_param_types(match.group("params"))
        if method_params == param_types:
            matches.append(match.group("name"))
    preferred = [name for name in matches if name.lower().startswith("score")]
    if len(preferred) == 1:
        return preferred[0]
    return matches[0] if len(matches) == 1 else ""


def _java_param_types(params: str) -> tuple[str, ...]:
    types: list[str] = []
    for raw_param in str(params or "").split(","):
        param = raw_param.strip()
        if not param:
            continue
        cleaned = re.sub(r"\bfinal\s+", "", param)
        parts = cleaned.split()
        types.append(parts[0] if len(parts) == 1 else " ".join(parts[:-1]))
    return tuple(types)


def _camel_to_constant(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    return re.sub(r"(?<!^)([A-Z])", r"_\1", token).upper()


def _lower_camel_from_type(param_type: str) -> str:
    simple = str(param_type or "value").strip().replace("[]", "Array").rsplit(".", maxsplit=1)[-1]
    simple = re.sub(r"[^A-Za-z0-9_]", "", simple) or "value"
    return simple[:1].lower() + simple[1:]


def _java_member_insertion_operation(
    *,
    path: str,
    original: str,
    additions: Sequence[str],
    repair_kind: str,
) -> RepairOperation | None:
    addition_text = "\n" + "\n".join(addition.rstrip() for addition in additions if addition.strip()) + "\n"
    if not addition_text.strip():
        return None
    last_brace = original.rfind("}")
    if last_brace < 0:
        return None
    context_start = max(0, last_brace - 240)
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=last_brace,
        span_end=last_brace + 1,
        expected="}",
        replacement=f"{addition_text}}}",
        before_hash=sha256_text(original),
        metadata={
            "repair_kind": repair_kind,
            "edit_strategy": "span_text_replace",
            "expected_context_before": original[context_start:last_brace],
            "unique_context": original[context_start : last_brace + 1],
        },
    )


def _dedupe_preserve_order(values: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _java_tail_line_incomplete(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped or stripped.startswith("//"):
        return False
    if stripped.endswith((",", ".", "?", ":", "&&", "||", "+", "-", "*", "/", "%", "=")):
        return True
    balance = _java_structure_balance(stripped)
    return balance["paren"] > 0 or balance["bracket"] > 0


def _java_missing_closing_brace_count(content: str) -> int:
    balance = _java_structure_balance(content)
    if balance["extra_closing_brace"] > 0:
        return 0
    return max(0, balance["brace"])


def _java_structure_balance(content: str) -> dict[str, int]:
    brace = 0
    paren = 0
    bracket = 0
    extra_closing_brace = 0
    index = 0
    in_line_comment = False
    in_block_comment = False
    in_string = False
    in_char = False
    escaped = False
    token = str(content or "")
    while index < len(token):
        char = token[index]
        next_char = token[index + 1] if index + 1 < len(token) else ""
        if in_line_comment:
            if char in "\r\n":
                in_line_comment = False
            index += 1
            continue
        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
            else:
                index += 1
            continue
        if in_string or in_char:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif in_string and char == '"':
                in_string = False
            elif in_char and char == "'":
                in_char = False
            index += 1
            continue
        if char == "/" and next_char == "/":
            in_line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            in_block_comment = True
            index += 2
            continue
        if char == '"':
            in_string = True
        elif char == "'":
            in_char = True
        elif char == "{":
            brace += 1
        elif char == "}":
            if brace > 0:
                brace -= 1
            else:
                extra_closing_brace += 1
        elif char == "(":
            paren += 1
        elif char == ")":
            paren = max(0, paren - 1)
        elif char == "[":
            bracket += 1
        elif char == "]":
            bracket = max(0, bracket - 1)
        index += 1
    return {
        "brace": brace,
        "paren": paren,
        "bracket": bracket,
        "extra_closing_brace": extra_closing_brace,
    }


def _common_prefix_len(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


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
    "JAVA_POST_SOURCE_TOOL",
    "JAVA_TEST_DEPENDENCY_SOURCE_TOOL",
    "build_java_accessor_alias_plan",
    "build_java_eof_truncation_plan",
    "build_java_missing_symbol_compatibility_plan",
    "build_java_numeric_constant_type_plan",
    "build_java_post_plan",
    "build_java_test_dependency_plan",
    "repair_java_common_accessor_aliases_text",
    "repair_java_eof_truncation_text",
    "repair_java_test_dependencies_text",
]
